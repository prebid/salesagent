import uuid
import warnings
from datetime import date, datetime, time

# --- V2.3 Pydantic Models (Bearer Auth, Restored & Complete) ---
# --- MCP Status System (AdCP PR #77) ---
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TaskStatus(str, Enum):
    """Standardized task status enum per AdCP MCP Status specification.

    Provides crystal clear guidance on when operations need clarification,
    approval, or other human input with consistent status handling across
    MCP and A2A protocols.
    """

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"
    UNKNOWN = "unknown"

    @classmethod
    def from_operation_state(
        cls, operation_type: str, has_errors: bool = False, requires_approval: bool = False, requires_auth: bool = False
    ) -> str:
        """Convert operation state to appropriate status for decision trees.

        Args:
            operation_type: Type of operation (discovery, creation, activation, etc.)
            has_errors: Whether the operation encountered errors
            requires_approval: Whether the operation requires human approval
            requires_auth: Whether the operation requires authentication

        Returns:
            Appropriate TaskStatus value for client decision making
        """
        if requires_auth:
            return cls.AUTH_REQUIRED
        if has_errors:
            return cls.FAILED
        if requires_approval:
            return cls.INPUT_REQUIRED
        if operation_type in ["discovery", "listing"]:
            return cls.COMPLETED  # Discovery operations complete immediately
        if operation_type in ["creation", "activation", "update"]:
            return cls.WORKING  # Async operations in progress
        return cls.UNKNOWN


# --- Core Models ---
class AssetRequirement(BaseModel):
    """Asset requirement specification per AdCP spec."""

    asset_type: str = Field(..., description="Type of asset required")
    quantity: int = Field(1, minimum=1, description="Number of assets of this type required")
    requirements: dict[str, Any] | None = Field(None, description="Specific requirements for this asset type")


class Format(BaseModel):
    format_id: str
    name: str
    type: Literal["video", "audio", "display", "native", "dooh"]  # Extended beyond spec
    is_standard: bool | None = Field(None, description="Whether this follows IAB standards")
    iab_specification: str | None = Field(None, description="Name of the IAB specification (if applicable)")
    requirements: dict[str, Any] | None = Field(
        None, description="Format-specific requirements (varies by format type)"
    )
    assets_required: list[AssetRequirement] | None = Field(
        None, description="Array of required assets for composite formats"
    )


# Format Registry for AdCP Compliance
# This registry converts format ID strings to Format objects for AdCP protocol responses
# Updated to support comprehensive modern advertising formats per AdCP standard
FORMAT_REGISTRY: dict[str, Format] = {
    # Standard IAB Display Formats
    "display_300x250": Format(
        format_id="display_300x250",
        name="Medium Rectangle",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 300, "height": 250, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    "display_728x90": Format(
        format_id="display_728x90",
        name="Leaderboard",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 728, "height": 90, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    "display_320x50": Format(
        format_id="display_320x50",
        name="Mobile Banner",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 320, "height": 50, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    "display_300x600": Format(
        format_id="display_300x600",
        name="Half Page Ad",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 300, "height": 600, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    "display_970x250": Format(
        format_id="display_970x250",
        name="Billboard",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 970, "height": 250, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    "display_970x90": Format(
        format_id="display_970x90",
        name="Super Leaderboard",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 970, "height": 90, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    # Additional Standard IAB Display Formats
    "display_160x600": Format(
        format_id="display_160x600",
        name="Wide Skyscraper",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 160, "height": 600, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    "display_320x480": Format(
        format_id="display_320x480",
        name="Mobile Interstitial",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 320, "height": 480, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    "display_336x280": Format(
        format_id="display_336x280",
        name="Large Rectangle",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 336, "height": 280, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    "display_970x550": Format(
        format_id="display_970x550",
        name="Panorama",
        type="display",
        is_standard=True,
        iab_specification="IAB Display",
        requirements={"width": 970, "height": 550, "file_types": ["jpg", "png", "gif", "html5"]},
    ),
    # Video Formats (Multiple Aspect Ratios & Resolutions)
    "video_640x360": Format(
        format_id="video_640x360",
        name="Video 360p (16:9)",
        type="video",
        is_standard=True,
        iab_specification="VAST 4.0",
        requirements={
            "width": 640,
            "height": 360,
            "duration_max": 30,
            "aspect_ratio": "16:9",
            "codecs": ["h264", "vp9"],
        },
    ),
    "video_1280x720": Format(
        format_id="video_1280x720",
        name="Video 720p HD (16:9)",
        type="video",
        is_standard=True,
        iab_specification="VAST 4.0",
        requirements={
            "width": 1280,
            "height": 720,
            "duration_max": 30,
            "aspect_ratio": "16:9",
            "codecs": ["h264", "vp9"],
        },
    ),
    "video_1920x1080": Format(
        format_id="video_1920x1080",
        name="Video 1080p Full HD (16:9)",
        type="video",
        is_standard=True,
        iab_specification="VAST 4.0",
        requirements={
            "width": 1920,
            "height": 1080,
            "duration_max": 30,
            "aspect_ratio": "16:9",
            "codecs": ["h264", "vp9"],
        },
    ),
    "video_1080x1920": Format(
        format_id="video_1080x1920",
        name="Vertical Video (9:16)",
        type="video",
        is_standard=True,
        iab_specification="VAST 4.0",
        requirements={
            "width": 1080,
            "height": 1920,
            "duration_max": 15,
            "aspect_ratio": "9:16",
            "codecs": ["h264", "vp9"],
        },
    ),
    "video_1080x1080": Format(
        format_id="video_1080x1080",
        name="Square Video (1:1)",
        type="video",
        is_standard=True,
        iab_specification="VAST 4.0",
        requirements={
            "width": 1080,
            "height": 1080,
            "duration_max": 15,
            "aspect_ratio": "1:1",
            "codecs": ["h264", "vp9"],
        },
    ),
    # Audio Formats
    "audio_15s": Format(
        format_id="audio_15s",
        name="Audio 15 Second Spot",
        type="audio",
        is_standard=True,
        iab_specification="DAAST 1.0",
        requirements={"duration": 15, "bitrate_min": 128, "formats": ["mp3", "aac"], "sample_rate": 44100},
    ),
    "audio_30s": Format(
        format_id="audio_30s",
        name="Audio 30 Second Spot",
        type="audio",
        is_standard=True,
        iab_specification="DAAST 1.0",
        requirements={"duration": 30, "bitrate_min": 128, "formats": ["mp3", "aac"], "sample_rate": 44100},
    ),
    "audio_60s": Format(
        format_id="audio_60s",
        name="Audio 60 Second Spot",
        type="audio",
        is_standard=True,
        iab_specification="DAAST 1.0",
        requirements={"duration": 60, "bitrate_min": 128, "formats": ["mp3", "aac"], "sample_rate": 44100},
    ),
    # Native Formats
    "native_article": Format(
        format_id="native_article",
        name="Native Article",
        type="native",
        is_standard=True,
        iab_specification="OpenRTB Native 1.2",
        requirements={"title_length": 25, "description_length": 90},
        assets_required=[
            AssetRequirement(asset_type="title", quantity=1, requirements={"max_characters": 25, "required": True}),
            AssetRequirement(
                asset_type="description", quantity=1, requirements={"max_characters": 90, "required": True}
            ),
            AssetRequirement(
                asset_type="image", quantity=1, requirements={"min_width": 300, "min_height": 200, "required": True}
            ),
        ],
    ),
    "native_feed": Format(
        format_id="native_feed",
        name="Native Feed Ad",
        type="native",
        is_standard=True,
        iab_specification="OpenRTB Native 1.2",
        requirements={"title_length": 50, "description_length": 150},
        assets_required=[
            AssetRequirement(asset_type="title", quantity=1, requirements={"max_characters": 50, "required": True}),
            AssetRequirement(
                asset_type="description", quantity=1, requirements={"max_characters": 150, "required": True}
            ),
            AssetRequirement(
                asset_type="image", quantity=1, requirements={"width": 1200, "height": 628, "required": True}
            ),
            AssetRequirement(
                asset_type="logo", quantity=1, requirements={"width": 200, "height": 200, "required": False}
            ),
        ],
    ),
    "native_content": Format(
        format_id="native_content",
        name="Native Content Ad",
        type="native",
        is_standard=True,
        iab_specification="OpenRTB Native 1.2",
        requirements={"headline_length": 60, "body_length": 200},
        assets_required=[
            AssetRequirement(asset_type="headline", quantity=1, requirements={"max_characters": 60, "required": True}),
            AssetRequirement(asset_type="body", quantity=1, requirements={"max_characters": 200, "required": True}),
            AssetRequirement(
                asset_type="image", quantity=1, requirements={"min_width": 600, "min_height": 400, "required": True}
            ),
            AssetRequirement(asset_type="cta", quantity=1, requirements={"max_characters": 15, "required": True}),
        ],
    ),
    # Digital Out-of-Home (DOOH) Formats
    "dooh_billboard_landscape": Format(
        format_id="dooh_billboard_landscape",
        name="Digital Billboard Landscape",
        type="dooh",
        is_standard=True,
        iab_specification="DOOH 2.0",
        requirements={"width": 1920, "height": 1080, "duration": 15, "file_types": ["jpg", "png", "mp4"]},
    ),
    "dooh_billboard_portrait": Format(
        format_id="dooh_billboard_portrait",
        name="Digital Billboard Portrait",
        type="dooh",
        is_standard=True,
        iab_specification="DOOH 2.0",
        requirements={"width": 1080, "height": 1920, "duration": 15, "file_types": ["jpg", "png", "mp4"]},
    ),
    "dooh_transit_screen": Format(
        format_id="dooh_transit_screen",
        name="Transit Digital Screen",
        type="dooh",
        is_standard=True,
        iab_specification="DOOH 2.0",
        requirements={"width": 1920, "height": 540, "duration": 10, "file_types": ["jpg", "png", "mp4"]},
    ),
    "dooh_mall_kiosk": Format(
        format_id="dooh_mall_kiosk",
        name="Mall Kiosk Display",
        type="dooh",
        is_standard=True,
        iab_specification="DOOH 2.0",
        requirements={
            "width": 1080,
            "height": 1920,
            "duration": 20,
            "file_types": ["jpg", "png", "mp4"],
            "interactive": True,
        },
    ),
    # Rich Media & Interactive Formats
    "rich_media_expandable": Format(
        format_id="rich_media_expandable",
        name="Expandable Rich Media",
        type="display",
        is_standard=True,
        iab_specification="MRAID 3.0",
        requirements={
            "collapsed_width": 300,
            "collapsed_height": 250,
            "expanded_width": 600,
            "expanded_height": 500,
            "file_types": ["html5"],
            "max_file_size_mb": 2,
        },
        assets_required=[
            AssetRequirement(
                asset_type="collapsed_creative",
                quantity=1,
                requirements={"width": 300, "height": 250, "required": True},
            ),
            AssetRequirement(
                asset_type="expanded_creative", quantity=1, requirements={"width": 600, "height": 500, "required": True}
            ),
        ],
    ),
    "rich_media_interstitial": Format(
        format_id="rich_media_interstitial",
        name="Rich Media Interstitial",
        type="display",
        is_standard=True,
        iab_specification="MRAID 3.0",
        requirements={"width": "100%", "height": "100%", "file_types": ["html5"], "max_file_size_mb": 5},
    ),
    # Connected TV (CTV) Formats
    "ctv_preroll": Format(
        format_id="ctv_preroll",
        name="Connected TV Pre-Roll",
        type="video",
        is_standard=True,
        iab_specification="VAST 4.0",
        requirements={
            "width": 1920,
            "height": 1080,
            "duration": 30,
            "aspect_ratio": "16:9",
            "codecs": ["h264"],
            "bitrate_min": 3000,
        },
    ),
    "ctv_midroll": Format(
        format_id="ctv_midroll",
        name="Connected TV Mid-Roll",
        type="video",
        is_standard=True,
        iab_specification="VAST 4.0",
        requirements={
            "width": 1920,
            "height": 1080,
            "duration": 15,
            "aspect_ratio": "16:9",
            "codecs": ["h264"],
            "bitrate_min": 3000,
        },
    ),
    # Social Media Optimized Formats
    "social_story": Format(
        format_id="social_story",
        name="Social Media Story",
        type="video",
        is_standard=False,
        requirements={"width": 1080, "height": 1920, "duration": 15, "aspect_ratio": "9:16", "codecs": ["h264", "vp9"]},
    ),
    "social_feed_video": Format(
        format_id="social_feed_video",
        name="Social Feed Video",
        type="video",
        is_standard=False,
        requirements={"width": 1080, "height": 1080, "duration": 30, "aspect_ratio": "1:1", "codecs": ["h264", "vp9"]},
    ),
    # Foundational Formats (AdCP Standard Extensions)
    "foundation_immersive_canvas": Format(
        format_id="foundation_immersive_canvas",
        name="Immersive Canvas",
        type="display",
        is_standard=True,
        iab_specification="AdCP Foundational",
        requirements={
            "responsive": True,
            "platforms": ["desktop", "tablet", "mobile"],
            "animation_allowed": True,
            "max_animation_duration_seconds": 30,
            "user_initiated_expansion": True,
        },
        assets_required=[
            AssetRequirement(
                asset_type="html",
                quantity=1,
                requirements={
                    "name": "Main Creative HTML",
                    "description": "Responsive HTML5 creative that adapts to different viewports",
                    "acceptable_formats": ["html", "html5"],
                    "max_file_size_mb": 5,
                    "responsive": True,
                    "required": True,
                },
            ),
            AssetRequirement(
                asset_type="image",
                quantity=1,
                requirements={
                    "name": "Backup Image",
                    "description": "Static backup image for environments that don't support HTML5",
                    "acceptable_formats": ["jpg", "png", "gif"],
                    "max_file_size_mb": 0.2,
                    "width": 970,
                    "height": 250,
                    "required": False,
                },
            ),
        ],
    ),
    "foundation_product_showcase_carousel": Format(
        format_id="foundation_product_showcase_carousel",
        name="Product Showcase Carousel",
        type="display",
        is_standard=True,
        iab_specification="AdCP Foundational",
        requirements={
            "product_count_min": 3,
            "product_count_max": 10,
            "aspect_ratio": "1:1",
        },
        assets_required=[
            AssetRequirement(
                asset_type="image",
                quantity=10,
                requirements={
                    "name": "Product Images",
                    "description": "Collection of product images for the carousel (3-10 images)",
                    "acceptable_formats": ["jpg", "png"],
                    "max_file_size_mb": 0.2,
                    "min_count": 3,
                    "max_count": 10,
                    "aspect_ratio": "1:1",
                    "required": True,
                },
            ),
            AssetRequirement(
                asset_type="text",
                quantity=10,
                requirements={
                    "name": "Product Titles",
                    "description": "Title text for each product in the carousel",
                    "max_length": 50,
                    "count": "matches product_images count",
                    "required": True,
                },
            ),
            AssetRequirement(
                asset_type="text",
                quantity=10,
                requirements={
                    "name": "Product Descriptions",
                    "description": "Description text for each product",
                    "max_length": 100,
                    "count": "matches product_images count",
                    "required": False,
                },
            ),
            AssetRequirement(
                asset_type="url",
                quantity=10,
                requirements={
                    "name": "Product Click-Through URLs",
                    "description": "Landing page URL for each product",
                    "count": "matches product_images count",
                    "required": True,
                },
            ),
        ],
    ),
    "foundation_expandable_display": Format(
        format_id="foundation_expandable_display",
        name="Expandable Display",
        type="display",
        is_standard=True,
        iab_specification="AdCP Foundational",
        requirements={"user_interaction_required": True, "close_button_required": True, "polite_load": True},
        assets_required=[
            AssetRequirement(
                asset_type="html",
                quantity=1,
                requirements={
                    "name": "Collapsed State Creative",
                    "description": "Creative shown in collapsed state",
                    "acceptable_formats": ["html", "html5", "jpg", "png"],
                    "max_file_size_mb": 1,
                    "dimensions": {
                        "desktop": {"width": 970, "height": 90},
                        "tablet": {"width": 728, "height": 90},
                        "mobile": {"width": 320, "height": 50},
                    },
                    "required": True,
                },
            ),
            AssetRequirement(
                asset_type="html",
                quantity=1,
                requirements={
                    "name": "Expanded State Creative",
                    "description": "Creative shown when expanded",
                    "acceptable_formats": ["html", "html5"],
                    "max_file_size_mb": 2,
                    "dimensions": {
                        "desktop": {"width": 970, "height": 500},
                        "tablet": {"width": 728, "height": 500},
                        "mobile": {"width": 320, "height": 480},
                    },
                    "animation_allowed": True,
                    "polite_load": True,
                    "close_button_required": True,
                    "required": True,
                },
            ),
        ],
    ),
    "foundation_scroll_triggered_experience": Format(
        format_id="foundation_scroll_triggered_experience",
        name="Scroll-Triggered Experience",
        type="display",
        is_standard=True,
        iab_specification="AdCP Foundational",
        requirements={
            "mobile_first": True,
            "trigger_type": "scroll",
            "trigger_threshold": "25%",
            "parallax_enabled": True,
            "sticky_duration_seconds": 5,
        },
        assets_required=[
            AssetRequirement(
                asset_type="html",
                quantity=1,
                requirements={
                    "name": "Main Content",
                    "description": "Primary creative content triggered on scroll",
                    "acceptable_formats": ["html", "html5"],
                    "max_file_size_mb": 3,
                    "platforms": ["mobile", "tablet"],
                    "dimensions": {
                        "mobile": {"width": "100vw", "height": "100vh"},
                        "tablet": {"width": "100vw", "height": "50vh"},
                    },
                    "required": True,
                },
            ),
            AssetRequirement(
                asset_type="video",
                quantity=1,
                requirements={
                    "name": "Background Video",
                    "description": "Optional background video for enhanced experience",
                    "acceptable_formats": ["mp4", "webm"],
                    "max_file_size_mb": 5,
                    "duration_seconds": 15,
                    "autoplay": True,
                    "muted": True,
                    "controls": False,
                    "loop": True,
                    "required": False,
                },
            ),
            AssetRequirement(
                asset_type="image",
                quantity=1,
                requirements={
                    "name": "Background Image",
                    "description": "Fallback image when video is not supported",
                    "acceptable_formats": ["jpg", "png"],
                    "max_file_size_mb": 1,
                    "required": False,
                },
            ),
        ],
    ),
    "foundation_universal_video": Format(
        format_id="foundation_universal_video",
        name="Universal Video",
        type="video",
        is_standard=True,
        iab_specification="AdCP Foundational",
        requirements={
            "aspect_ratios": ["16:9", "9:16", "1:1", "4:5"],
            "duration_range": {"min": 6, "max": 30, "extended_max": 180},
            "codecs": ["h264", "vp9"],
            "max_bitrate_mbps": 10,
        },
        assets_required=[
            AssetRequirement(
                asset_type="video",
                quantity=1,
                requirements={
                    "name": "Video File",
                    "description": "Main video creative file",
                    "acceptable_formats": ["mp4", "webm"],
                    "max_file_size_mb": 50,
                    "duration_seconds": 30,
                    "max_bitrate_mbps": 10,
                    "aspect_ratios": ["16:9", "9:16", "1:1", "4:5"],
                    "codecs": ["h264", "vp9"],
                    "audio": {"codec": "aac", "bitrate_kbps": 128, "muted_by_default": True},
                    "required": True,
                },
            ),
            AssetRequirement(
                asset_type="text",
                quantity=1,
                requirements={
                    "name": "Captions",
                    "description": "Caption file for accessibility",
                    "acceptable_formats": ["srt", "vtt"],
                    "burned_in_alternative": "Captions can be burned into the video file",
                    "required": True,
                },
            ),
            AssetRequirement(
                asset_type="image",
                quantity=1,
                requirements={
                    "name": "Companion Banner",
                    "description": "Static companion banner for video ads",
                    "acceptable_formats": ["jpg", "png"],
                    "max_file_size_mb": 0.2,
                    "width": 300,
                    "height": 250,
                    "required": False,
                },
            ),
        ],
    ),
    # HTML5 Interactive Format (for testing and interactive content)
    "html5_interactive": Format(
        format_id="html5_interactive",
        name="HTML5 Interactive Banner",
        type="display",
        is_standard=False,
        iab_specification="HTML5",
        requirements={"width": 300, "height": 250, "file_types": ["html5", "zip"], "interactive": True},
        assets_required=[
            AssetRequirement(
                asset_type="html",
                quantity=1,
                requirements={
                    "name": "Interactive HTML5 Creative",
                    "description": "Interactive HTML5 creative with assets",
                    "acceptable_formats": ["html", "html5", "zip"],
                    "max_file_size_mb": 5,
                    "width": 300,
                    "height": 250,
                    "interactive": True,
                    "required": True,
                },
            ),
        ],
    ),
}


def get_format_by_id(format_id: str) -> Format | None:
    """Get a Format object by its ID."""
    return FORMAT_REGISTRY.get(format_id)


def convert_format_ids_to_formats(format_ids: list[str]) -> list[Format]:
    """Convert a list of format ID strings to Format objects.

    This function is used to ensure AdCP schema compliance by converting
    internal format ID representations to full Format objects.
    """
    formats = []
    for format_id in format_ids:
        format_obj = get_format_by_id(format_id)
        if format_obj:
            formats.append(format_obj)
        else:
            # For unknown format IDs, create a minimal Format object
            formats.append(
                Format(
                    format_id=format_id, name=format_id.replace("_", " ").title(), type="display"  # Default to display
                )
            )
    return formats


class FrequencyCap(BaseModel):
    """Simple frequency capping configuration.

    Provides basic impression suppression at the media buy or package level.
    More sophisticated frequency management is handled by the AXE layer.
    """

    suppress_minutes: int = Field(..., gt=0, description="Suppress impressions for this many minutes after serving")
    scope: Literal["media_buy", "package"] = Field("media_buy", description="Apply at media buy or package level")


class TargetingCapability(BaseModel):
    """Defines targeting dimension capabilities and restrictions."""

    dimension: str  # e.g., "geo_country", "key_value"
    access: Literal["overlay", "managed_only", "both"] = "overlay"
    description: str | None = None
    allowed_values: list[str] | None = None  # For restricted value sets
    axe_signal: bool | None = False  # Whether this is an AXE signal dimension


class Targeting(BaseModel):
    """Comprehensive targeting options for media buys.

    All fields are optional and can be combined for precise audience targeting.
    Platform adapters will map these to their specific targeting capabilities.
    Uses any_of/none_of pattern for consistent include/exclude across all dimensions.

    Note: Some targeting dimensions are managed-only and cannot be set via overlay.
    These are typically used for AXE signal integration.
    """

    # Geographic targeting - aligned with OpenRTB (overlay access)
    geo_country_any_of: list[str] | None = None  # ISO country codes: ["US", "CA", "GB"]
    geo_country_none_of: list[str] | None = None

    geo_region_any_of: list[str] | None = None  # Region codes: ["NY", "CA", "ON"]
    geo_region_none_of: list[str] | None = None

    geo_metro_any_of: list[str] | None = None  # Metro/DMA codes: ["501", "803"]
    geo_metro_none_of: list[str] | None = None

    geo_city_any_of: list[str] | None = None  # City names: ["New York", "Los Angeles"]
    geo_city_none_of: list[str] | None = None

    geo_zip_any_of: list[str] | None = None  # Postal codes: ["10001", "90210"]
    geo_zip_none_of: list[str] | None = None

    # Device and platform targeting
    device_type_any_of: list[str] | None = None  # ["mobile", "desktop", "tablet", "ctv", "audio", "dooh"]
    device_type_none_of: list[str] | None = None

    os_any_of: list[str] | None = None  # Operating systems: ["iOS", "Android", "Windows"]
    os_none_of: list[str] | None = None

    browser_any_of: list[str] | None = None  # Browsers: ["Chrome", "Safari", "Firefox"]
    browser_none_of: list[str] | None = None

    # Content and contextual targeting
    content_cat_any_of: list[str] | None = None  # IAB content categories
    content_cat_none_of: list[str] | None = None

    keywords_any_of: list[str] | None = None  # Keyword targeting
    keywords_none_of: list[str] | None = None

    # Audience targeting
    audiences_any_of: list[str] | None = None  # Audience segments
    audiences_none_of: list[str] | None = None

    # Signal targeting - can use signal IDs from get_signals endpoint
    signals: list[str] | None = None  # Signal IDs like ["auto_intenders_q1_2025", "sports_content"]

    # Media type targeting
    media_type_any_of: list[str] | None = None  # ["video", "audio", "display", "native"]
    media_type_none_of: list[str] | None = None

    # Frequency control
    frequency_cap: FrequencyCap | None = None  # Impression limits per user/period

    # Connection type targeting
    connection_type_any_of: list[int] | None = None  # OpenRTB connection types
    connection_type_none_of: list[int] | None = None

    # Platform-specific custom targeting
    custom: dict[str, Any] | None = None  # Platform-specific targeting options

    # Key-value targeting (managed-only for AXE signals)
    # These are not exposed in overlay - only set by orchestrator/AXE
    key_value_pairs: dict[str, str] | None = None  # e.g., {"aee_segment": "high_value", "aee_score": "0.85"}

    # Internal fields (not in AdCP spec)
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy")
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp")
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp")
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata")

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal and managed fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal and managed fields to exclude by default
            exclude.update(
                {
                    "key_value_pairs",  # Managed-only field
                    "tenant_id",
                    "created_at",
                    "updated_at",
                    "metadata",  # Internal fields
                }
            )
            kwargs["exclude"] = exclude

        return super().model_dump(**kwargs)

    def model_dump_internal(self, **kwargs):
        """Dump including internal and managed fields for database storage and internal processing."""
        # Don't exclude internal fields or managed fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)

    def dict(self, **kwargs):
        """Override dict to always exclude managed fields (for backward compat)."""
        kwargs["exclude"] = kwargs.get("exclude", set())
        if isinstance(kwargs["exclude"], set):
            kwargs["exclude"].add("key_value_pairs")
        return super().dict(**kwargs)


class Budget(BaseModel):
    """Budget object with multi-currency support."""

    total: float = Field(..., description="Total budget amount")
    currency: str = Field(..., description="ISO 4217 currency code (e.g., 'USD', 'EUR')")
    daily_cap: float | None = Field(None, description="Optional daily spending limit")
    pacing: Literal["even", "asap", "daily_budget"] = Field("even", description="Budget pacing strategy")


# AdCP Compliance Models
class Measurement(BaseModel):
    """Measurement capabilities included with a product per AdCP spec."""

    type: str = Field(
        ..., description="Type of measurement", examples=["incremental_sales_lift", "brand_lift", "foot_traffic"]
    )
    attribution: str = Field(
        ..., description="Attribution methodology", examples=["deterministic_purchase", "probabilistic"]
    )
    window: str | None = Field(None, description="Attribution window", examples=["30_days", "7_days"])
    reporting: str = Field(
        ..., description="Reporting frequency and format", examples=["weekly_dashboard", "real_time_api"]
    )


class CreativePolicy(BaseModel):
    """Creative requirements and restrictions for a product per AdCP spec."""

    co_branding: Literal["required", "optional", "none"] = Field(..., description="Co-branding requirement")
    landing_page: Literal["any", "retailer_site_only", "must_include_retailer"] = Field(
        ..., description="Landing page requirements"
    )
    templates_available: bool = Field(..., description="Whether creative templates are provided")


class Product(BaseModel):
    product_id: str
    name: str
    description: str
    formats: list[str]  # Internal field name for backward compatibility
    delivery_type: Literal["guaranteed", "non_guaranteed"]
    is_fixed_price: bool
    cpm: float | None = None
    min_spend: float | None = Field(None, description="Minimum budget requirement in USD", gt=-1)
    measurement: Measurement | None = Field(None, description="Measurement capabilities included with this product")
    creative_policy: CreativePolicy | None = Field(None, description="Creative requirements and restrictions")
    is_custom: bool = Field(default=False)
    brief_relevance: str | None = Field(
        None, description="Explanation of why this product matches the brief (populated when brief is provided)"
    )
    expires_at: datetime | None = None
    implementation_config: dict[str, Any] | None = Field(
        default=None,
        description="Ad server-specific configuration for implementing this product (placements, line item settings, etc.)",
    )

    @property
    def format_ids(self) -> list[str]:
        """AdCP spec compliant property name for formats."""
        return self.formats

    def model_dump(self, **kwargs):
        """Return AdCP-compliant model dump with proper field names, excluding internal fields and null values."""
        # Exclude internal/non-spec fields
        kwargs["exclude"] = kwargs.get("exclude", set())
        if isinstance(kwargs["exclude"], set):
            kwargs["exclude"].update({"implementation_config", "expires_at"})

        data = super().model_dump(**kwargs)

        # Convert formats to format_ids per AdCP spec
        if "formats" in data:
            data["format_ids"] = data.pop("formats")

        # Remove null fields per AdCP spec but keep core pricing fields
        # Core fields that should be present even if None for AdCP compliance
        core_fields = {
            "cpm",
            "min_spend",
            "product_id",
            "name",
            "description",
            "format_ids",
            "delivery_type",
            "is_fixed_price",
            "is_custom",
        }

        adcp_data = {}
        for key, value in data.items():
            # Include core fields always, and non-null optional fields
            if key in core_fields or value is not None:
                adcp_data[key] = value

        return adcp_data

    def model_dump_internal(self, **kwargs):
        """Return internal model dump including all fields for database operations."""
        return super().model_dump(**kwargs)

    def model_dump_adcp_compliant(self, **kwargs):
        """Return model dump for AdCP schema compliance."""
        return self.model_dump(**kwargs)

    def dict(self, **kwargs):
        """Override dict to maintain backward compatibility."""
        return self.model_dump(**kwargs)


# --- Core Schemas ---


class Principal(BaseModel):
    """Principal object containing authentication and adapter mapping information."""

    principal_id: str
    name: str
    platform_mappings: dict[str, Any]

    def get_adapter_id(self, adapter_name: str) -> str | None:
        """Get the adapter-specific ID for this principal."""
        # Map adapter short names to platform keys
        adapter_platform_map = {
            "gam": "google_ad_manager",
            "google_ad_manager": "google_ad_manager",
            "kevel": "kevel",
            "triton": "triton",
            "mock": "mock",
        }

        platform_key = adapter_platform_map.get(adapter_name)
        if not platform_key:
            return None

        platform_data = self.platform_mappings.get(platform_key, {})
        if isinstance(platform_data, dict):
            # Try common field names for advertiser ID
            for field in ["advertiser_id", "id", "company_id"]:
                if field in platform_data:
                    return str(platform_data[field]) if platform_data[field] else None

        # Fallback to old format for backwards compatibility
        old_field_map = {
            "gam": "gam_advertiser_id",
            "kevel": "kevel_advertiser_id",
            "triton": "triton_advertiser_id",
            "mock": "mock_advertiser_id",
        }
        old_field = old_field_map.get(adapter_name)
        if old_field and old_field in self.platform_mappings:
            return str(self.platform_mappings[old_field]) if self.platform_mappings[old_field] else None

        return None


# --- Performance Index ---
class ProductPerformance(BaseModel):
    product_id: str
    performance_index: float  # 1.0 = baseline, 1.2 = 20% better, 0.8 = 20% worse
    confidence_score: float | None = None  # 0.0 to 1.0


class UpdatePerformanceIndexRequest(BaseModel):
    media_buy_id: str
    performance_data: list[ProductPerformance]


class UpdatePerformanceIndexResponse(BaseModel):
    status: str
    detail: str


# --- Discovery ---
class GetProductsRequest(BaseModel):
    brief: str
    promoted_offering: str = Field(
        ...,
        description="Description of the advertiser and the product or service being promoted (REQUIRED per AdCP spec)",
    )
    strategy_id: str | None = Field(
        None,
        description="Optional strategy ID for linking operations and enabling simulation/testing modes",
    )


class Error(BaseModel):
    """Standard error structure per AdCP spec."""

    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    details: dict[str, Any] | None = Field(None, description="Additional error details")


class GetProductsResponse(BaseModel):
    """Response for get_products tool.

    Now only contains AdCP spec fields. Context management is handled
    automatically by the MCP wrapper at the protocol layer.
    """

    products: list[Product]
    message: str | None = None  # Optional human-readable message
    errors: list[Error] | None = None  # Optional error reporting
    status: str | None = Field(None, description="Optional task status per AdCP MCP Status specification")

    def model_dump(self, **kwargs):
        """Override to ensure products use AdCP-compliant serialization."""
        # Get basic structure
        data = {}

        # Serialize products using their custom model_dump method
        if self.products:
            data["products"] = [product.model_dump(**kwargs) for product in self.products]
        else:
            data["products"] = []

        # Add other fields, excluding None values for AdCP compliance
        if self.message is not None:
            data["message"] = self.message
        if self.errors is not None:
            data["errors"] = self.errors
        if self.status is not None:
            data["status"] = self.status

        return data


class ListCreativeFormatsResponse(BaseModel):
    """Response for list_creative_formats tool.

    Returns comprehensive Format objects per AdCP specification.
    Context management is handled automatically by the MCP wrapper at the protocol layer.
    """

    formats: list[Format]  # Full Format objects per AdCP spec
    message: str | None = None  # Optional human-readable message
    errors: list[Error] | None = None  # Optional error reporting
    specification_version: str | None = Field(None, description="AdCP format specification version")
    status: str | None = Field(None, description="Optional task status per AdCP MCP Status specification")


# --- Creative Lifecycle ---
class CreativeGroup(BaseModel):
    """Groups creatives for organizational and management purposes."""

    group_id: str
    principal_id: str
    name: str
    description: str | None = None
    created_at: datetime
    tags: list[str] | None = []


class Creative(BaseModel):
    """Individual creative asset in the creative library - AdCP spec compliant."""

    # Core identification fields
    creative_id: str
    name: str

    # AdCP spec compliant fields
    format: str = Field(alias="format_id", description="Creative format type per AdCP spec")
    url: str = Field(alias="content_uri", description="URL of the creative content per AdCP spec")
    media_url: str | None = Field(None, description="Alternative media URL (typically same as url)")
    click_url: str | None = Field(None, alias="click_through_url", description="Landing page URL per AdCP spec")

    # Content dimensions and properties (AdCP spec)
    duration: float | None = Field(None, description="Duration in seconds (for video/audio)", gt=-1)
    width: int | None = Field(None, description="Width in pixels (for video/display)", gt=-1)
    height: int | None = Field(None, description="Height in pixels (for video/display)", gt=-1)

    # Creative status and review (AdCP spec)
    status: str = Field(default="pending", description="Creative status per AdCP spec")
    platform_id: str | None = Field(None, description="Platform-specific ID assigned to the creative")
    review_feedback: str | None = Field(None, description="Feedback from platform review (if any)")

    # Compliance information (AdCP spec)
    compliance: dict[str, Any] | None = Field(None, description="Compliance review status")

    # Package assignments (AdCP spec)
    package_assignments: list[str] | None = Field(
        None, description="Package IDs or buyer_refs to assign this creative to"
    )

    # Multi-asset support (AdCP spec)
    assets: list[dict[str, Any]] | None = Field(None, description="For multi-asset formats like carousels")

    # === AdCP v1.3+ Creative Management Fields ===
    # Fully compliant with AdCP specification for third-party tags and native creatives

    snippet: str | None = Field(
        None, description="HTML/JS/VAST snippet for third-party creatives (mutually exclusive with media_url)"
    )

    snippet_type: Literal["html", "javascript", "vast_xml", "vast_url"] | None = Field(
        None, description="Type of snippet content (required when snippet is provided)"
    )

    template_variables: dict[str, Any] | None = Field(
        None,
        description="Variables for native ad templates per AdCP spec",
        example={
            "headline": "Amazing Product",
            "body": "This product will change your life",
            "main_image_url": "https://cdn.example.com/product.jpg",
            "logo_url": "https://cdn.example.com/logo.png",
            "cta_text": "Shop Now",
            "advertiser_name": "Brand Name",
            "price": "$99.99",
            "star_rating": "4.5",
        },
    )

    # Platform-specific extension (not in core AdCP spec)
    delivery_settings: dict[str, Any] | None = Field(
        None,
        description="Platform-specific delivery configuration (extension)",
        example={
            "safe_frame_compatible": True,
            "ssl_required": True,
            "orientation_lock": "FREE_ORIENTATION",
            "tracking_urls": ["https://..."],
        },
    )

    # Internal fields (not in AdCP spec, but available for internal use)
    principal_id: str  # Internal - not in AdCP spec
    group_id: str | None = None  # Internal - not in AdCP spec
    created_at: datetime  # Internal timestamp
    updated_at: datetime  # Internal timestamp
    has_macros: bool | None = False  # Internal processing
    macro_validation: dict[str, Any] | None = None  # Internal processing
    asset_mapping: dict[str, str] | None = Field(default_factory=dict)  # Internal mapping
    metadata: dict[str, Any] | None = Field(default_factory=dict)  # Internal metadata

    # Backward compatibility properties (deprecated)
    @property
    def format_id(self) -> str:
        """Backward compatibility for format_id.

        DEPRECATED: Use format instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "format_id is deprecated and will be removed in a future version. " "Use format instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.format

    @property
    def content_uri(self) -> str:
        """Backward compatibility for content_uri.

        DEPRECATED: Use url instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "content_uri is deprecated and will be removed in a future version. " "Use url instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.url

    @property
    def click_through_url(self) -> str | None:
        """Backward compatibility for click_through_url.

        DEPRECATED: Use click_url instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "click_through_url is deprecated and will be removed in a future version. " "Use click_url instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.click_url

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal fields to exclude by default for AdCP compliance
            exclude.update(
                {
                    "principal_id",
                    "group_id",
                    "created_at",
                    "updated_at",
                    "has_macros",
                    "macro_validation",
                    "asset_mapping",
                    "metadata",
                    # Extended delivery fields (our implementation-specific extensions)
                    # These can be included by explicitly requesting them
                    "content_type",
                    "content",
                    "delivery_settings",
                }
            )
            kwargs["exclude"] = exclude

        data = super().model_dump(**kwargs)

        # Ensure media_url defaults to url if not set (AdCP spec requirement)
        if "media_url" in data and data["media_url"] is None and "url" in data:
            data["media_url"] = data["url"]

        # Set default compliance status if not provided
        if "compliance" in data and data["compliance"] is None:
            data["compliance"] = {"status": "pending", "issues": []}

        return data

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        # Don't exclude internal fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)

    # === AdCP v1.3+ Helper Methods ===

    def get_creative_type(self) -> str:
        """Determine the creative type based on AdCP v1.3+ fields."""
        if self.snippet and self.snippet_type:
            if self.snippet_type in ["vast_xml", "vast_url"]:
                return "vast"
            else:
                return "third_party_tag"
        elif self.template_variables:
            return "native"
        elif self.media_url or (self.url and not self._is_html_snippet(self.url)):
            return "hosted_asset"
        elif self._is_html_snippet(self.url):
            # Auto-detect from URL for legacy support
            return "third_party_tag"
        else:
            return "hosted_asset"  # Default

    def _is_html_snippet(self, content: str) -> bool:
        """Detect if content is HTML/JS snippet rather than URL."""
        if not content:
            return False

        # Check for HTML/JS indicators
        html_indicators = ["<script", "<iframe", "<ins", "<div", "<span", "document.write", "innerHTML"]
        return any(indicator in content for indicator in html_indicators)

    def get_snippet_content(self) -> str | None:
        """Get the snippet content for third-party creatives (AdCP v1.3+ field)."""
        if self.snippet:
            return self.snippet
        elif self._is_html_snippet(self.url):
            return self.url  # Auto-detect from URL
        return None

    def get_template_variables_dict(self) -> dict[str, Any] | None:
        """Get native template variables (AdCP v1.3+ field)."""
        return self.template_variables

    def get_primary_content_url(self) -> str:
        """Get the primary content URL for hosted assets."""
        return self.media_url or self.url

    def set_third_party_snippet(self, snippet: str, snippet_type: str, settings: dict = None):
        """Convenience method to set up a third-party tag creative (AdCP v1.3+)."""
        self.snippet = snippet
        self.snippet_type = snippet_type
        if settings:
            self.delivery_settings = settings

    def set_native_template_variables(self, template_vars: dict[str, Any], settings: dict = None):
        """Convenience method to set up a native creative (AdCP v1.3+)."""
        self.template_variables = template_vars
        if settings:
            self.delivery_settings = settings

    @model_validator(mode="after")
    def validate_creative_fields(self) -> "Creative":
        """Validate AdCP creative field requirements and mutual exclusivity."""
        # Check mutual exclusivity: media_url XOR snippet
        has_media = bool(self.media_url or (self.url and not self._is_html_snippet(self.url)))
        has_snippet = bool(self.snippet)

        if has_media and has_snippet:
            raise ValueError("Creative cannot have both media content and snippet - they are mutually exclusive")

        # Validate snippet_type is provided when snippet is present
        if self.snippet and not self.snippet_type:
            raise ValueError("snippet_type is required when snippet is provided")

        # Validate snippet_type values
        if self.snippet_type and not self.snippet:
            raise ValueError("snippet is required when snippet_type is provided")

        return self


class CreativeAdaptation(BaseModel):
    """Suggested adaptation or variant of a creative."""

    adaptation_id: str
    format_id: str
    name: str
    description: str
    preview_url: str | None = None
    changes_summary: list[str] = Field(default_factory=list)
    rationale: str | None = None
    estimated_performance_lift: float | None = None  # Percentage improvement expected


class CreativeStatus(BaseModel):
    creative_id: str
    status: Literal["pending_review", "approved", "rejected", "adaptation_required"]
    detail: str
    estimated_approval_time: datetime | None = None
    suggested_adaptations: list[CreativeAdaptation] = Field(default_factory=list)


class CreativeAssignment(BaseModel):
    """Maps creatives to packages with distribution control."""

    assignment_id: str
    media_buy_id: str
    package_id: str
    creative_id: str

    # Distribution control
    weight: int | None = 100  # Relative weight for rotation
    percentage_goal: float | None = None  # Percentage of impressions
    rotation_type: Literal["weighted", "sequential", "even"] | None = "weighted"

    # Override settings (platform-specific)
    override_click_url: str | None = None
    override_start_date: datetime | None = None
    override_end_date: datetime | None = None

    # Targeting override (creative-specific targeting)
    targeting_overlay: Targeting | None = None

    is_active: bool = True


class AddCreativeAssetsRequest(BaseModel):
    """Request to add creative assets to a media buy (AdCP spec compliant)."""

    media_buy_id: str | None = None
    buyer_ref: str | None = None
    assets: list[Creative]  # Renamed from 'creatives' to match spec

    def model_validate(cls, values):
        # Ensure at least one of media_buy_id or buyer_ref is provided
        if not values.get("media_buy_id") and not values.get("buyer_ref"):
            raise ValueError("Either media_buy_id or buyer_ref must be provided")
        return values

    # Backward compatibility
    @property
    def creatives(self) -> list[Creative]:
        """Backward compatibility for existing code."""
        return self.assets


class AddCreativeAssetsResponse(BaseModel):
    """Response from adding creative assets (AdCP spec compliant)."""

    statuses: list[CreativeStatus]


# Legacy aliases for backward compatibility (to be removed)
SubmitCreativesRequest = AddCreativeAssetsRequest
SubmitCreativesResponse = AddCreativeAssetsResponse


class SyncCreativesRequest(BaseModel):
    """Request to sync creative assets to centralized library (AdCP spec compliant)."""

    media_buy_id: str | None = Field(None, description="Publisher's ID of the media buy")
    buyer_ref: str | None = Field(None, description="Buyer's reference for the media buy")
    creatives: list[Creative] = Field(..., description="Array of creative assets to sync")
    assign_to_packages: list[str] | None = Field(None, description="Package IDs to assign creatives to")
    upsert: bool = Field(True, description="Whether to update existing creatives or create new ones")

    @model_validator(mode="before")
    def validate_media_buy_reference(cls, values):
        """Ensure at least one of media_buy_id or buyer_ref is provided."""
        if not values.get("media_buy_id") and not values.get("buyer_ref"):
            raise ValueError("Either media_buy_id or buyer_ref must be provided")
        return values


class SyncCreativesResponse(BaseModel):
    """Response from syncing creative assets (AdCP spec compliant)."""

    synced_creatives: list[Creative] = Field(..., description="Successfully synced creatives")
    failed_creatives: list[dict[str, Any]] = Field(
        default_factory=list, description="Failed creatives with error details"
    )
    assignments: list[CreativeAssignment] = Field(default_factory=list, description="Creative assignments to packages")
    message: str | None = Field(None, description="Human-readable status message")


class ListCreativesRequest(BaseModel):
    """Request to list and search creative library (AdCP spec compliant)."""

    media_buy_id: str | None = Field(None, description="Filter by media buy ID")
    buyer_ref: str | None = Field(None, description="Filter by buyer reference")
    status: str | None = Field(None, description="Filter by creative status (pending, approved, rejected)")
    format: str | None = Field(None, description="Filter by creative format")
    tags: list[str] | None = Field(None, description="Filter by tags")
    created_after: datetime | None = Field(None, description="Filter by creation date")
    created_before: datetime | None = Field(None, description="Filter by creation date")
    search: str | None = Field(None, description="Search in creative names and descriptions")
    page: int = Field(1, ge=1, description="Page number for pagination")
    limit: int = Field(50, ge=1, le=1000, description="Number of results per page")
    sort_by: str | None = Field("created_date", description="Sort field (created_date, name, status)")
    sort_order: Literal["asc", "desc"] = Field("desc", description="Sort order")


class ListCreativesResponse(BaseModel):
    """Response from listing creative assets (AdCP spec compliant)."""

    creatives: list[Creative] = Field(..., description="Array of creative assets")
    total_count: int = Field(..., description="Total number of creatives matching filters")
    page: int = Field(..., description="Current page number")
    limit: int = Field(..., description="Results per page")
    has_more: bool = Field(..., description="Whether more pages are available")
    message: str | None = Field(None, description="Human-readable status message")


class CheckCreativeStatusRequest(BaseModel):
    creative_ids: list[str]


class CheckCreativeStatusResponse(BaseModel):
    statuses: list[CreativeStatus]


# New creative management endpoints
class CreateCreativeGroupRequest(BaseModel):
    name: str
    description: str | None = None
    tags: list[str] | None = []


class CreateCreativeGroupResponse(BaseModel):
    group: CreativeGroup


class CreateCreativeRequest(BaseModel):
    """Create a creative in the library (not tied to a media buy)."""

    group_id: str | None = None
    format_id: str
    content_uri: str
    name: str
    click_through_url: str | None = None
    metadata: dict[str, Any] | None = {}


class CreateCreativeResponse(BaseModel):
    creative: Creative
    status: CreativeStatus
    suggested_adaptations: list[CreativeAdaptation] = Field(default_factory=list)


class AssignCreativeRequest(BaseModel):
    """Assign a creative from the library to a package."""

    media_buy_id: str
    package_id: str
    creative_id: str
    weight: int | None = 100
    percentage_goal: float | None = None
    rotation_type: Literal["weighted", "sequential", "even"] | None = "weighted"
    override_click_url: str | None = None
    override_start_date: datetime | None = None
    override_end_date: datetime | None = None
    targeting_overlay: Targeting | None = None


class AssignCreativeResponse(BaseModel):
    assignment: CreativeAssignment


class GetCreativesRequest(BaseModel):
    """Get creatives with optional filtering."""

    group_id: str | None = None
    media_buy_id: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    include_assignments: bool = False


class GetCreativesResponse(BaseModel):
    creatives: list[Creative]
    assignments: list[CreativeAssignment] | None = None


# Admin tools
class GetPendingCreativesRequest(BaseModel):
    """Admin-only: Get all pending creatives across all principals."""

    principal_id: str | None = None  # Filter by principal if specified
    limit: int | None = 100


class GetPendingCreativesResponse(BaseModel):
    pending_creatives: list[dict[str, Any]]  # Includes creative + principal info


class ApproveCreativeRequest(BaseModel):
    """Admin-only: Approve or reject a creative."""

    creative_id: str
    action: Literal["approve", "reject"]
    reason: str | None = None


class ApproveCreativeResponse(BaseModel):
    creative_id: str
    new_status: str
    detail: str


class AdaptCreativeRequest(BaseModel):
    media_buy_id: str
    original_creative_id: str
    target_format_id: str
    new_creative_id: str
    instructions: str | None = None


class Package(BaseModel):
    """Package object - AdCP spec compliant.

    Note: In create-media-buy-request, clients only provide buyer_ref+products.
    Server generates package_id and sets initial status per AdCP package schema.
    """

    # AdCP Package object fields (required in responses, generated during creation)
    package_id: str | None = Field(None, description="Publisher's unique identifier for the package")
    status: Literal["draft", "active", "paused", "completed"] | None = Field(None, description="Status of the package")

    # AdCP optional fields
    buyer_ref: str | None = Field(None, description="Buyer's reference identifier for this package")
    product_id: str | None = Field(None, description="ID of the product this package is based on (single product)")
    products: list[str] | None = Field(None, description="Array of product IDs to include in this package")
    budget: Budget | None = Field(None, description="Package-specific budget")
    impressions: float | None = Field(None, description="Impression goal for this package", gt=-1)
    targeting_overlay: Targeting | None = Field(None, description="Package-specific targeting")
    creative_assignments: list[dict[str, Any]] | None = Field(
        None, description="Creative assets assigned to this package"
    )

    # Internal fields (not in AdCP spec)
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy")
    media_buy_id: str | None = Field(None, description="Internal: Associated media buy ID")
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp")
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp")
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata")

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal fields to exclude by default
            exclude.update({"tenant_id", "media_buy_id", "created_at", "updated_at", "metadata"})
            kwargs["exclude"] = exclude

        data = super().model_dump(**kwargs)

        # Ensure required AdCP fields are present for responses
        # (These should be set during package creation/processing)
        if data.get("package_id") is None:
            raise ValueError("Package missing required package_id for AdCP response")
        if data.get("status") is None:
            raise ValueError("Package missing required status for AdCP response")

        return data

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        # Don't exclude internal fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)


# --- Media Buy Lifecycle ---
class CreateMediaBuyRequest(BaseModel):
    # New AdCP v2.4 fields (optional for backward compatibility)
    buyer_ref: str | None = Field(None, description="Buyer reference for tracking")
    packages: list[Package] | None = Field(None, description="Array of packages with products and budgets")
    start_time: datetime | None = Field(None, description="Campaign start time (ISO 8601)")
    end_time: datetime | None = Field(None, description="Campaign end time (ISO 8601)")
    budget: Budget | None = Field(None, description="Overall campaign budget")

    # Legacy fields (for backward compatibility)
    product_ids: list[str] | None = Field(None, description="Legacy: Product IDs (converted to packages)")
    start_date: date | None = Field(None, description="Legacy: Start date (converted to start_time)")
    end_date: date | None = Field(None, description="Legacy: End date (converted to end_time)")
    total_budget: float | None = Field(None, description="Legacy: Total budget (converted to Budget object)")

    # Common fields
    targeting_overlay: Targeting | None = None
    po_number: str = Field(..., description="Purchase order number for tracking (REQUIRED per AdCP spec)")
    pacing: Literal["even", "asap", "daily_budget"] = "even"  # Legacy field
    daily_budget: float | None = None  # Legacy field
    creatives: list[Creative] | None = None
    # AXE signal requirements
    required_axe_signals: list[str] | None = None  # Required targeting signals
    enable_creative_macro: bool | None = False  # Enable AXE to provide creative_macro signal
    strategy_id: str | None = Field(
        None,
        description="Optional strategy ID for linking operations and enabling simulation/testing modes",
    )

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_format(cls, values):
        """Convert legacy format to new format."""
        if not isinstance(values, dict):
            return values

        # If using legacy format, convert to new format
        if "product_ids" in values and not values.get("packages"):
            # Generate buyer_ref if not provided
            if not values.get("buyer_ref"):
                values["buyer_ref"] = f"buy_{uuid.uuid4().hex[:8]}"

            # Convert product_ids to packages
            # Note: AdCP create-media-buy-request only requires buyer_ref+products from client
            # Server generates package_id and initial status per AdCP package schema
            product_ids = values.get("product_ids", [])
            packages = []
            for i, pid in enumerate(product_ids):
                package_uuid = uuid.uuid4().hex[:6]
                packages.append(
                    {
                        "package_id": f"pkg_{i}_{package_uuid}",  # Server-generated per AdCP spec
                        "buyer_ref": f"pkg_{i}_{package_uuid}",  # Client reference for tracking
                        "products": [pid],
                        "status": "draft",  # Server sets initial status per AdCP package schema
                    }
                )
            values["packages"] = packages

        # Convert dates to datetimes
        if "start_date" in values and not values.get("start_time"):
            start_date = values["start_date"]
            if isinstance(start_date, str):
                start_date = date.fromisoformat(start_date)
            values["start_time"] = datetime.combine(start_date, time.min)

        if "end_date" in values and not values.get("end_time"):
            end_date = values["end_date"]
            if isinstance(end_date, str):
                end_date = date.fromisoformat(end_date)
            values["end_time"] = datetime.combine(end_date, time.max)

        # Convert total_budget to Budget object
        if "total_budget" in values and not values.get("budget"):
            total_budget = values["total_budget"]
            pacing = values.get("pacing", "even")
            daily_cap = values.get("daily_budget")

            values["budget"] = {
                "total": total_budget,
                "currency": "USD",  # Default currency
                "pacing": pacing,
                "daily_cap": daily_cap,
            }

        # Ensure required fields are present after conversion
        if not values.get("buyer_ref"):
            values["buyer_ref"] = f"buy_{uuid.uuid4().hex[:8]}"

        return values

    # Backward compatibility properties for old field names
    @property
    def flight_start_date(self) -> date:
        """Backward compatibility for old field name."""
        return self.start_time.date() if self.start_time else None

    @property
    def flight_end_date(self) -> date:
        """Backward compatibility for old field name."""
        return self.end_time.date() if self.end_time else None

    def get_total_budget(self) -> float:
        """Get total budget, handling both new and legacy formats."""
        if self.budget:
            return self.budget.total
        return self.total_budget or 0.0

    def get_product_ids(self) -> list[str]:
        """Extract all product IDs from packages for backward compatibility."""
        if self.packages:
            product_ids = []
            for package in self.packages:
                product_ids.extend(package.products)
            return product_ids
        return self.product_ids or []


class CreateMediaBuyResponse(BaseModel):
    """Response from create_media_buy operation.

    This is an async operation that may require manual approval or additional steps.
    The status field indicates the current state of the media buy creation.
    """

    media_buy_id: str
    buyer_ref: str | None = None  # May not have buyer_ref if failed
    status: str | None = None  # TaskStatus values: submitted, working, input-required, completed, failed, etc.
    detail: str | None = None  # Additional status details
    message: str | None = None  # Human-readable message
    packages: list[dict[str, Any]] = Field(default_factory=list, description="Created packages with IDs")
    creative_deadline: datetime | None = None
    errors: list[Error] | None = None  # Protocol-compliant error reporting


class CheckMediaBuyStatusRequest(BaseModel):
    media_buy_id: str | None = None
    buyer_ref: str | None = None
    strategy_id: str | None = Field(
        None,
        description="Optional strategy ID for consistent simulation/testing context",
    )

    def model_validate(cls, values):
        # Ensure at least one of media_buy_id or buyer_ref is provided
        if not values.get("media_buy_id") and not values.get("buyer_ref"):
            raise ValueError("Either media_buy_id or buyer_ref must be provided")
        return values


class CheckMediaBuyStatusResponse(BaseModel):
    media_buy_id: str
    buyer_ref: str
    status: str  # pending_creative, active, paused, completed, failed
    packages: list[dict[str, Any]] | None = None
    budget_spent: Budget | None = None
    budget_remaining: Budget | None = None
    creative_count: int = 0


class LegacyUpdateMediaBuyRequest(BaseModel):
    """Legacy update request - kept for backward compatibility."""

    media_buy_id: str
    new_budget: float | None = None
    new_targeting_overlay: Targeting | None = None
    creative_assignments: dict[str, list[str]] | None = None


class GetMediaBuyDeliveryRequest(BaseModel):
    """Request delivery data for one or more media buys.

    Examples:
    - Single buy: media_buy_ids=["buy_123"]
    - Multiple buys: buyer_refs=["ref_123", "ref_456"]
    - All active buys: status_filter="active"
    - All buys: status_filter="all"
    """

    media_buy_ids: list[str] | None = Field(
        None, description="Specific media buy IDs to fetch. If omitted, fetches based on status_filter."
    )
    buyer_refs: list[str] | None = Field(
        None, description="Alternative: specify buyer references instead of media buy IDs."
    )
    status_filter: str | None = Field(
        "active",
        description="Filter for which buys to fetch when IDs/refs not provided: 'active', 'all', 'completed'",
    )
    today: date = Field(..., description="Reference date for calculating delivery metrics")
    strategy_id: str | None = Field(
        None,
        description="Optional strategy ID for consistent simulation/testing context",
    )


class MediaBuyDeliveryData(BaseModel):
    """Delivery data for a single media buy."""

    media_buy_id: str
    buyer_ref: str
    status: str
    spend: Budget
    impressions: int
    pacing: str
    days_elapsed: int
    total_days: int


class GetMediaBuyDeliveryResponse(BaseModel):
    """Response containing delivery data for requested media buys.

    For single buy requests, 'deliveries' will contain one item.
    For multiple/all requests, it contains all matching buys.
    """

    deliveries: list[MediaBuyDeliveryData]
    total_spend: float
    total_impressions: int
    active_count: int
    summary_date: date


# Deprecated - kept for backward compatibility
class GetAllMediaBuyDeliveryRequest(BaseModel):
    """DEPRECATED: Use GetMediaBuyDeliveryRequest with filter='all' instead."""

    today: date
    media_buy_ids: list[str] | None = None


class GetAllMediaBuyDeliveryResponse(BaseModel):
    """DEPRECATED: Use GetMediaBuyDeliveryResponse instead."""

    deliveries: list[MediaBuyDeliveryData]
    total_spend: float
    total_impressions: int
    active_count: int
    summary_date: date


# --- Additional Schema Classes ---
class MediaPackage(BaseModel):
    package_id: str
    name: str
    delivery_type: Literal["guaranteed", "non_guaranteed"]
    cpm: float
    impressions: int
    format_ids: list[str]


class ReportingPeriod(BaseModel):
    start: datetime
    end: datetime
    start_date: date | None = None  # For compatibility
    end_date: date | None = None  # For compatibility


class DeliveryTotals(BaseModel):
    impressions: int
    spend: float
    clicks: int | None = 0
    video_completions: int | None = 0


class PackagePerformance(BaseModel):
    package_id: str
    performance_index: float


class AssetStatus(BaseModel):
    creative_id: str
    status: str


class UpdateMediaBuyResponse(BaseModel):
    status: str
    implementation_date: datetime | None = None
    reason: str | None = None
    detail: str | None = None


# Unified update models
class PackageUpdate(BaseModel):
    """Updates to apply to a specific package."""

    package_id: str
    active: bool | None = None  # True to activate, False to pause
    budget: float | None = None  # New budget in dollars
    impressions: int | None = None  # Direct impression goal (overrides budget calculation)
    cpm: float | None = None  # Update CPM rate
    daily_budget: float | None = None  # Daily spend cap
    daily_impressions: int | None = None  # Daily impression cap
    pacing: Literal["even", "asap", "front_loaded"] | None = None
    creative_ids: list[str] | None = None  # Update creative assignments
    targeting_overlay: Targeting | None = None  # Package-specific targeting refinements


class UpdatePackageRequest(BaseModel):
    """Update one or more packages within a media buy.

    Uses PATCH semantics: Only packages mentioned are affected.
    Omitted packages remain unchanged.
    To remove a package from delivery, set active=false.
    To add new packages, use create_media_buy or add_packages (future tool).
    """

    media_buy_id: str
    packages: list[PackageUpdate]  # List of package updates
    today: date | None = None  # For testing/simulation


class UpdateMediaBuyRequest(BaseModel):
    """Update a media buy - mirrors CreateMediaBuyRequest structure.

    Uses PATCH semantics: Only fields provided are updated.
    Package updates only affect packages explicitly mentioned.
    To pause all packages, set active=false at campaign level.
    To pause specific packages, include them in packages list with active=false.
    """

    media_buy_id: str
    # Campaign-level updates
    buyer_ref: str | None = None  # Update buyer reference
    active: bool | None = None  # True to activate, False to pause entire campaign
    flight_start_date: date | None = None  # Change start date (if not started)
    flight_end_date: date | None = None  # Extend or shorten campaign
    budget: Budget | float | None = None  # Update total budget (supports Budget object or float)
    currency: str | None = None  # Update currency (ISO 4217)
    targeting_overlay: Targeting | None = None  # Update global targeting
    start_time: datetime | None = None  # Update start datetime
    end_time: datetime | None = None  # Update end datetime
    pacing: Literal["even", "asap", "daily_budget"] | None = None
    daily_budget: float | None = None  # Daily spend cap across all packages
    # Package-level updates
    packages: list[PackageUpdate] | None = None  # Package-specific updates (only these are affected)
    # Creative updates
    creatives: list[Creative] | None = None  # Add new creatives

    # Backward compatibility properties
    @property
    def total_budget(self) -> float | None:
        """Backward compatibility for old field name."""
        return self.budget

    @property
    def start_date(self) -> date | None:
        """Alias for consistency with CreateMediaBuyRequest."""
        return self.flight_start_date

    @property
    def end_date(self) -> date | None:
        """Alias for consistency with CreateMediaBuyRequest."""
        return self.flight_end_date

    # Legacy fields
    creative_assignments: dict[str, list[str]] | None = None  # Update creative-to-package mapping
    today: date | None = None  # For testing/simulation
    strategy_id: str | None = Field(
        None,
        description="Optional strategy ID for consistent simulation/testing context",
    )


# Adapter-specific response schemas
class PackageDelivery(BaseModel):
    package_id: str
    impressions: int
    spend: float


class AdapterGetMediaBuyDeliveryResponse(BaseModel):
    """Response from adapter's get_media_buy_delivery method"""

    media_buy_id: str
    reporting_period: ReportingPeriod
    totals: DeliveryTotals
    by_package: list[PackageDelivery]
    currency: str


# --- Human-in-the-Loop Task Queue ---


class HumanTask(BaseModel):
    """Task requiring human intervention."""

    task_id: str
    task_type: (
        str  # creative_approval, permission_exception, configuration_required, compliance_review, manual_approval
    )
    principal_id: str
    adapter_name: str | None = None
    status: str = "pending"  # pending, assigned, in_progress, completed, failed, escalated
    priority: str = "medium"  # low, medium, high, urgent

    # Context
    media_buy_id: str | None = None
    creative_id: str | None = None
    operation: str | None = None
    error_detail: str | None = None
    context_data: dict[str, Any] | None = None

    # Assignment
    assigned_to: str | None = None
    assigned_at: datetime | None = None

    # Timing
    created_at: datetime
    updated_at: datetime
    due_by: datetime | None = None
    completed_at: datetime | None = None

    # Resolution
    resolution: str | None = None  # approved, rejected, completed, cannot_complete
    resolution_detail: str | None = None
    resolved_by: str | None = None


class CreateHumanTaskRequest(BaseModel):
    """Request to create a human task."""

    task_type: str
    priority: str = "medium"
    adapter_name: str | None = None  # Added to match HumanTask schema

    # Context
    media_buy_id: str | None = None
    creative_id: str | None = None
    operation: str | None = None
    error_detail: str | None = None
    context_data: dict[str, Any] | None = None

    # SLA
    due_in_hours: int | None = None  # Hours until due


class CreateHumanTaskResponse(BaseModel):
    """Response from creating a human task."""

    task_id: str
    status: str
    due_by: datetime | None = None


class GetPendingTasksRequest(BaseModel):
    """Request for pending human tasks."""

    principal_id: str | None = None  # Filter by principal
    task_type: str | None = None  # Filter by type
    priority: str | None = None  # Filter by minimum priority
    assigned_to: str | None = None  # Filter by assignee
    include_overdue: bool = True


class GetPendingTasksResponse(BaseModel):
    """Response with pending tasks."""

    tasks: list[HumanTask]
    total_count: int
    overdue_count: int


class AssignTaskRequest(BaseModel):
    """Request to assign a task."""

    task_id: str
    assigned_to: str


class CompleteTaskRequest(BaseModel):
    """Request to complete a task."""

    task_id: str
    resolution: str  # approved, rejected, completed, cannot_complete
    resolution_detail: str | None = None
    resolved_by: str


class VerifyTaskRequest(BaseModel):
    """Request to verify if a task was completed correctly."""

    task_id: str
    expected_outcome: dict[str, Any] | None = None  # What the task should have accomplished


class VerifyTaskResponse(BaseModel):
    """Response from task verification."""

    task_id: str
    verified: bool
    actual_state: dict[str, Any]
    expected_state: dict[str, Any] | None = None
    discrepancies: list[str] = []


class MarkTaskCompleteRequest(BaseModel):
    """Admin request to mark a task as complete with verification."""

    task_id: str
    override_verification: bool = False  # Force complete even if verification fails
    completed_by: str


# Targeting capabilities
class GetTargetingCapabilitiesRequest(BaseModel):
    """Query targeting capabilities for channels."""

    channels: list[str] | None = None  # If None, return all channels
    include_aee_dimensions: bool = True


class TargetingDimensionInfo(BaseModel):
    """Information about a single targeting dimension."""

    key: str
    display_name: str
    description: str
    data_type: str
    required: bool = False
    values: list[str] | None = None


class ChannelTargetingCapabilities(BaseModel):
    """Targeting capabilities for a specific channel."""

    channel: str
    overlay_dimensions: list[TargetingDimensionInfo]
    aee_dimensions: list[TargetingDimensionInfo] | None = None


class GetTargetingCapabilitiesResponse(BaseModel):
    """Response with targeting capabilities."""

    capabilities: list[ChannelTargetingCapabilities]


class CheckAXERequirementsRequest(BaseModel):
    """Check if required AXE dimensions are supported."""

    channel: str
    required_dimensions: list[str]


class CheckAXERequirementsResponse(BaseModel):
    """Response for AXE requirements check."""

    supported: bool
    missing_dimensions: list[str]
    available_dimensions: list[str]


# Creative macro is now a simple string passed via AXE axe_signals


# --- Signal Discovery ---
class SignalDeployment(BaseModel):
    """Platform deployment information for a signal - AdCP spec compliant."""

    platform: str = Field(..., description="Platform name")
    account: str | None = Field(None, description="Specific account if applicable")
    is_live: bool = Field(..., description="Whether signal is currently active")
    scope: Literal["platform-wide", "account-specific"] = Field(..., description="Deployment scope")
    decisioning_platform_segment_id: str | None = Field(None, description="Platform-specific segment ID")
    estimated_activation_duration_minutes: float | None = Field(None, description="Time to activate if not live", gt=-1)


class SignalPricing(BaseModel):
    """Pricing information for a signal - AdCP spec compliant."""

    cpm: float = Field(..., description="Cost per thousand impressions", gt=-1)
    currency: str = Field(..., description="Currency code", pattern="^[A-Z]{3}$")


class Signal(BaseModel):
    """Represents an available signal - AdCP spec compliant."""

    # Core AdCP fields (required)
    signal_agent_segment_id: str = Field(..., description="Unique identifier for the signal")
    name: str = Field(..., description="Human-readable signal name")
    description: str = Field(..., description="Detailed signal description")
    signal_type: Literal["marketplace", "custom", "owned"] = Field(..., description="Type of signal")
    data_provider: str = Field(..., description="Name of the data provider")
    coverage_percentage: float = Field(..., description="Percentage of audience coverage", gt=-1, le=100)
    deployments: list[SignalDeployment] = Field(..., description="Array of platform deployments")
    pricing: SignalPricing = Field(..., description="Pricing information")

    # Internal fields (not in AdCP spec)
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy")
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp")
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp")
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata")

    # Backward compatibility properties (deprecated)
    @property
    def signal_id(self) -> str:
        """Backward compatibility for signal_id.

        DEPRECATED: Use signal_agent_segment_id instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "signal_id is deprecated and will be removed in a future version. " "Use signal_agent_segment_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.signal_agent_segment_id

    @property
    def type(self) -> str:
        """Backward compatibility for type.

        DEPRECATED: Use signal_type instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "type is deprecated and will be removed in a future version. " "Use signal_type instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.signal_type

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal fields to exclude by default
            exclude.update({"tenant_id", "created_at", "updated_at", "metadata"})
            kwargs["exclude"] = exclude

        return super().model_dump(**kwargs)

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        # Don't exclude internal fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)


class GetSignalsRequest(BaseModel):
    """Request to discover available signals."""

    query: str | None = None  # Natural language search query
    type: str | None = None  # Filter by signal type
    category: str | None = None  # Filter by category
    limit: int | None = 100


class GetSignalsResponse(BaseModel):
    """Response containing available signals."""

    signals: list[Signal]
    status: str | None = Field(None, description="Optional task status per AdCP MCP Status specification")


# --- Signal Activation ---
class ActivateSignalRequest(BaseModel):
    """Request to activate a signal for use in campaigns."""

    signal_id: str = Field(..., description="Signal ID to activate")
    campaign_id: str | None = Field(None, description="Optional campaign ID to activate signal for")
    media_buy_id: str | None = Field(None, description="Optional media buy ID to activate signal for")


class ActivateSignalResponse(BaseModel):
    """Response from signal activation."""

    signal_id: str = Field(..., description="Activated signal ID")
    status: str = Field(..., description="Task status per AdCP MCP Status specification")
    message: str | None = Field(None, description="Human-readable status message")
    activation_details: dict[str, Any] | None = Field(None, description="Platform-specific activation details")
    errors: list[Error] | None = Field(None, description="Optional error reporting")


# --- Simulation and Time Progression Control ---
class SimulationControlRequest(BaseModel):
    """Control simulation time progression and events."""

    strategy_id: str = Field(..., description="Strategy ID to control (must be simulation strategy with 'sim_' prefix)")
    action: Literal["jump_to", "reset", "set_scenario"] = Field(..., description="Action to perform on the simulation")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Action-specific parameters")


class SimulationControlResponse(BaseModel):
    """Response from simulation control operations."""

    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    current_state: dict[str, Any] | None = None
    simulation_time: datetime | None = None


# --- Authorized Properties Constants ---

# Valid property types per AdCP specification
PROPERTY_TYPES = ["website", "mobile_app", "ctv_app", "dooh", "podcast", "radio", "streaming_audio"]

# Valid verification statuses
VERIFICATION_STATUSES = ["pending", "verified", "failed"]

# Valid identifier types by property type (AdCP compliant mappings)
IDENTIFIER_TYPES_BY_PROPERTY_TYPE = {
    "website": ["domain", "subdomain"],
    "mobile_app": ["bundle_id", "store_id"],
    "ctv_app": ["roku_store_id", "amazon_store_id", "samsung_store_id", "lg_store_id"],
    "dooh": ["venue_id", "network_id"],
    "podcast": ["podcast_guid", "rss_feed_url"],
    "radio": ["station_call_sign", "stream_url"],
    "streaming_audio": ["platform_id", "stream_id"],
}

# Property form field requirements
PROPERTY_REQUIRED_FIELDS = ["property_type", "name", "identifiers", "publisher_domain"]

# Property form validation rules
PROPERTY_VALIDATION_RULES = {
    "name": {"min_length": 1, "max_length": 255},
    "publisher_domain": {"min_length": 1, "max_length": 255},
    "property_type": {"allowed_values": PROPERTY_TYPES},
    "verification_status": {"allowed_values": VERIFICATION_STATUSES},
    "tag_id": {"pattern": r"^[a-z0-9_]+$", "max_length": 50},
}

# Supported file types for bulk upload
SUPPORTED_UPLOAD_FILE_TYPES = [".json", ".csv"]

# Property form error messages
PROPERTY_ERROR_MESSAGES = {
    "missing_required_field": "Property type, name, and publisher domain are required",
    "invalid_property_type": "Invalid property type: {property_type}. Must be one of: {valid_types}",
    "invalid_file_type": "Only JSON and CSV files are supported",
    "no_file_selected": "No file selected",
    "at_least_one_identifier": "At least one identifier is required",
    "identifier_incomplete": "Identifier {index}: Both type and value are required",
    "invalid_json": "Invalid JSON format: {error}",
    "invalid_tag_id": "Tag ID must contain only letters, numbers, and underscores",
    "tag_already_exists": "Tag '{tag_id}' already exists",
    "all_fields_required": "All fields are required",
    "property_not_found": "Property not found",
    "tenant_not_found": "Tenant not found",
}


# --- Authorized Properties (AdCP Spec) ---
class PropertyIdentifier(BaseModel):
    """Identifier for an advertising property."""

    type: str = Field(
        ..., description="Type of identifier (e.g., 'domain', 'bundle_id', 'roku_store_id', 'podcast_guid')"
    )
    value: str = Field(
        ...,
        description="The identifier value. For domain type: 'example.com' matches www.example.com and m.example.com only; 'subdomain.example.com' matches that specific subdomain; '*.example.com' matches all subdomains",
    )


class Property(BaseModel):
    """An advertising property that can be validated via adagents.json (AdCP spec)."""

    property_type: Literal["website", "mobile_app", "ctv_app", "dooh", "podcast", "radio", "streaming_audio"] = Field(
        ..., description="Type of advertising property"
    )
    name: str = Field(..., description="Human-readable property name")
    identifiers: list[PropertyIdentifier] = Field(
        ..., min_length=1, description="Array of identifiers for this property"
    )
    tags: list[str] | None = Field(
        None, description="Tags for categorization and grouping (e.g., network membership, content categories)"
    )
    publisher_domain: str = Field(
        ..., description="Domain where adagents.json should be checked for authorization validation"
    )

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Return AdCP-compliant property representation."""
        data = super().model_dump(**kwargs)
        # Ensure tags is always present per AdCP schema
        if data.get("tags") is None:
            data["tags"] = []
        return data


class PropertyTagMetadata(BaseModel):
    """Metadata for a property tag."""

    name: str = Field(..., description="Human-readable name for this tag")
    description: str = Field(..., description="Description of what this tag represents")


class ListAuthorizedPropertiesRequest(BaseModel):
    """Request parameters for discovering all properties this agent is authorized to represent (AdCP spec)."""

    adcp_version: str = Field(
        default="1.0.0", pattern=r"^\d+\.\d+\.\d+$", description="AdCP schema version for this request"
    )
    tags: list[str] | None = Field(None, description="Filter properties by specific tags (optional)")

    @model_validator(mode="before")
    @classmethod
    def normalize_tags(cls, data):
        """Ensure tags are lowercase with underscores only."""
        if isinstance(data, dict) and "tags" in data and data["tags"]:
            data["tags"] = [tag.lower().replace("-", "_") for tag in data["tags"]]
        return data


class ListAuthorizedPropertiesResponse(BaseModel):
    """Response payload for list_authorized_properties task (AdCP spec)."""

    adcp_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$", description="AdCP schema version used for this response")
    properties: list[Property] = Field(..., description="Array of all properties this agent is authorized to represent")
    tags: dict[str, PropertyTagMetadata] = Field(
        default_factory=dict, description="Metadata for each tag referenced by properties"
    )
    errors: list[dict[str, Any]] | None = Field(
        None, description="Task-specific errors and warnings (e.g., property availability issues)"
    )

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Return AdCP-compliant response."""
        data = super().model_dump(**kwargs)
        # Ensure errors is always present per AdCP schema
        if data.get("errors") is None:
            data["errors"] = []
        return data
