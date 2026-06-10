"""
Channel-specific targeting dimensions for AdCP.

Defines which targeting dimensions are available for:
1. Targeting overlays (buyer-specified targeting)
2. AXE signals (orchestrator-provided, limited to: include, exclude, creative macros)

Per channel (audio, web, mobile app, CTV, DOOH).

Note: AXE signals are specifically limited to three types and can use custom
key names in the ad server. AXE is not used beyond include/exclude/macros.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class Channel(StrEnum):
    """Advertising channels."""

    AUDIO = "audio"
    WEB = "web"
    MOBILE_APP = "mobile_app"
    CTV = "ctv"
    DOOH = "dooh"


class TargetingDimension(BaseModel):
    """A single targeting dimension."""

    key: str
    display_name: str
    description: str
    data_type: str  # string, number, boolean, array, object
    required: bool = False
    values: list[str] | None = None  # For enum types


class TargetingCapabilities(BaseModel):
    """Targeting capabilities for a channel."""

    channel: Channel
    overlay_dimensions: list[TargetingDimension] = Field(
        description="Dimensions available for buyer targeting overlays"
    )
    axe_dimensions: list[TargetingDimension] = Field(
        description="Dimensions for AXE signals (include/exclude/macros only)"
    )


# Common dimensions across all channels
COMMON_OVERLAY_DIMENSIONS = [
    TargetingDimension(
        key="browser",
        display_name="Browser",
        description="Web browser type and version",
        data_type="array",
        values=["chrome", "firefox", "safari", "edge", "other"],
    ),
    TargetingDimension(
        key="device_type",
        display_name="Device Type",
        description="Type of device",
        data_type="array",
        values=["desktop", "mobile", "tablet", "connected_tv", "smart_speaker"],
    ),
    TargetingDimension(
        key="os",
        display_name="Operating System",
        description="Device operating system",
        data_type="array",
        values=["windows", "macos", "ios", "android", "linux", "roku", "tvos", "other"],
    ),
    TargetingDimension(
        key="language", display_name="Language", description="User's language preference", data_type="array"
    ),
    TargetingDimension(
        key="country", display_name="Country", description="User's country (ISO 3166-1 alpha-2)", data_type="array"
    ),
    TargetingDimension(
        key="region", display_name="Region/State", description="User's region or state", data_type="array"
    ),
    TargetingDimension(key="metro", display_name="Metro/DMA", description="Metro area or DMA code", data_type="array"),
    TargetingDimension(key="city", display_name="City", description="User's city", data_type="array"),
    TargetingDimension(
        key="user_ids", display_name="User IDs", description="Available user identity providers", data_type="object"
    ),
]

# Additional AXE dimensions (beyond overlay dimensions)
COMMON_AXE_DIMENSIONS = [
    TargetingDimension(
        key="postal_code", display_name="Postal Code", description="User's postal/ZIP code", data_type="string"
    ),
    TargetingDimension(
        key="postal_district",
        display_name="Postal District",
        description="Postal district (first part of postal code)",
        data_type="string",
    ),
    TargetingDimension(
        key="lat_long", display_name="Latitude/Longitude", description="Geographic coordinates", data_type="object"
    ),
]

# Channel-specific dimensions
AUDIO_SPECIFIC_OVERLAY = [
    TargetingDimension(
        key="genre",
        display_name="Genre",
        description="Audio content genre",
        data_type="array",
        values=["music", "news", "sports", "talk", "comedy", "true_crime", "business", "technology", "health"],
    ),
    TargetingDimension(
        key="content_rating",
        display_name="Content Rating",
        description="Content maturity rating",
        data_type="array",
        values=["all", "teen", "mature"],
    ),
    TargetingDimension(
        key="content_duration",
        display_name="Content Duration",
        description="Length of content in seconds",
        data_type="object",
    ),
    TargetingDimension(
        key="station_channel",
        display_name="Station/Channel",
        description="Radio station or podcast channel",
        data_type="array",
    ),
]

AUDIO_SPECIFIC_AXE = [
    TargetingDimension(
        key="podcast_episode_id",
        display_name="Podcast Episode ID",
        description="Unique podcast episode identifier (GUID)",
        data_type="string",
    ),
    TargetingDimension(
        key="podcast_show_name",
        display_name="Podcast Show Name",
        description="Name of the podcast show",
        data_type="string",
    ),
]

CTV_SPECIFIC_OVERLAY = [
    TargetingDimension(
        key="genre",
        display_name="Genre",
        description="Video content genre",
        data_type="array",
        values=["drama", "comedy", "news", "sports", "documentary", "reality", "kids", "movies"],
    ),
    TargetingDimension(
        key="content_rating",
        display_name="Content Rating",
        description="TV/Movie rating",
        data_type="array",
        values=["G", "PG", "PG-13", "TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14", "TV-MA"],
    ),
    TargetingDimension(
        key="content_duration",
        display_name="Content Duration",
        description="Length of content in seconds",
        data_type="object",
    ),
    TargetingDimension(
        key="channel_network",
        display_name="Channel/Network",
        description="TV channel or streaming service",
        data_type="array",
    ),
]

CTV_SPECIFIC_AXE = [
    TargetingDimension(
        key="show_name", display_name="Show Name", description="Name of the TV show or movie", data_type="string"
    ),
    TargetingDimension(
        key="show_metadata",
        display_name="Show Metadata",
        description="Additional show information (season, episode, etc.)",
        data_type="object",
    ),
    TargetingDimension(
        key="content_ids",
        display_name="Content IDs",
        description="Industry-standard content identifiers",
        data_type="object",
    ),
    TargetingDimension(
        key="iris_id", display_name="IRIS.TV ID", description="IRIS.TV content identifier", data_type="string"
    ),
    TargetingDimension(
        key="gracenote_id", display_name="Gracenote ID", description="Gracenote content identifier", data_type="string"
    ),
]

WEB_SPECIFIC_OVERLAY = [
    TargetingDimension(
        key="content_categories",
        display_name="Content Categories",
        description="IAB content categories",
        data_type="array",
    ),
    TargetingDimension(
        key="keywords", display_name="Keywords", description="Page keywords for contextual targeting", data_type="array"
    ),
]

WEB_SPECIFIC_AXE = [
    TargetingDimension(
        key="page_url", display_name="Page URL", description="Current page URL", data_type="string", required=True
    ),
    TargetingDimension(
        key="referrer_url", display_name="Referrer URL", description="Referring page URL", data_type="string"
    ),
    TargetingDimension(
        key="ad_slot_id", display_name="Ad Slot ID", description="Specific ad slot identifier", data_type="string"
    ),
    TargetingDimension(
        key="gpid",
        display_name="Global Placement ID",
        description="Standardized placement identifier",
        data_type="string",
    ),
    TargetingDimension(
        key="adjacent_content",
        display_name="Adjacent Content",
        description="List of content adjacent to ad placement",
        data_type="array",
    ),
]

MOBILE_APP_SPECIFIC_OVERLAY = [
    TargetingDimension(
        key="app_bundle", display_name="App Bundle ID", description="Mobile app bundle identifier", data_type="array"
    ),
    TargetingDimension(
        key="app_categories", display_name="App Categories", description="App store categories", data_type="array"
    ),
]

MOBILE_APP_SPECIFIC_AXE = [
    TargetingDimension(
        key="app_bundle_id",
        display_name="App Bundle ID",
        description="Current app's bundle identifier",
        data_type="string",
        required=True,
    ),
    TargetingDimension(
        key="app_version", display_name="App Version", description="Current app version", data_type="string"
    ),
    TargetingDimension(
        key="content_url",
        display_name="Content URL",
        description="URL for web-available content within app",
        data_type="string",
    ),
    TargetingDimension(
        key="content_id", display_name="Content ID", description="Internal content identifier", data_type="string"
    ),
    TargetingDimension(
        key="screen_name", display_name="Screen Name", description="Current screen or view name", data_type="string"
    ),
]

DOOH_SPECIFIC_OVERLAY = [
    TargetingDimension(
        key="venue_type",
        display_name="Venue Type",
        description="Type of venue",
        data_type="array",
        values=["transit", "retail", "office", "gym", "restaurant", "gas_station", "airport", "mall"],
    ),
    TargetingDimension(
        key="screen_size", display_name="Screen Size", description="Physical screen dimensions", data_type="array"
    ),
]

DOOH_SPECIFIC_AXE = [
    TargetingDimension(
        key="venue_id", display_name="Venue ID", description="Unique venue identifier", data_type="string"
    ),
    TargetingDimension(
        key="screen_id", display_name="Screen ID", description="Unique screen identifier", data_type="string"
    ),
    TargetingDimension(
        key="venue_metadata",
        display_name="Venue Metadata",
        description="Additional venue information",
        data_type="object",
    ),
    TargetingDimension(
        key="foot_traffic", display_name="Foot Traffic", description="Estimated foot traffic data", data_type="object"
    ),
]

# Channel capabilities mapping
CHANNEL_CAPABILITIES: dict[Channel, TargetingCapabilities] = {
    Channel.AUDIO: TargetingCapabilities(
        channel=Channel.AUDIO,
        overlay_dimensions=COMMON_OVERLAY_DIMENSIONS + AUDIO_SPECIFIC_OVERLAY,
        axe_dimensions=COMMON_OVERLAY_DIMENSIONS + AUDIO_SPECIFIC_OVERLAY + COMMON_AXE_DIMENSIONS + AUDIO_SPECIFIC_AXE,
    ),
    Channel.WEB: TargetingCapabilities(
        channel=Channel.WEB,
        overlay_dimensions=COMMON_OVERLAY_DIMENSIONS + WEB_SPECIFIC_OVERLAY,
        axe_dimensions=COMMON_OVERLAY_DIMENSIONS + WEB_SPECIFIC_OVERLAY + COMMON_AXE_DIMENSIONS + WEB_SPECIFIC_AXE,
    ),
    Channel.MOBILE_APP: TargetingCapabilities(
        channel=Channel.MOBILE_APP,
        overlay_dimensions=COMMON_OVERLAY_DIMENSIONS + MOBILE_APP_SPECIFIC_OVERLAY,
        axe_dimensions=COMMON_OVERLAY_DIMENSIONS
        + MOBILE_APP_SPECIFIC_OVERLAY
        + COMMON_AXE_DIMENSIONS
        + MOBILE_APP_SPECIFIC_AXE,
    ),
    Channel.CTV: TargetingCapabilities(
        channel=Channel.CTV,
        overlay_dimensions=COMMON_OVERLAY_DIMENSIONS + CTV_SPECIFIC_OVERLAY,
        axe_dimensions=COMMON_OVERLAY_DIMENSIONS + CTV_SPECIFIC_OVERLAY + COMMON_AXE_DIMENSIONS + CTV_SPECIFIC_AXE,
    ),
    Channel.DOOH: TargetingCapabilities(
        channel=Channel.DOOH,
        overlay_dimensions=COMMON_OVERLAY_DIMENSIONS + DOOH_SPECIFIC_OVERLAY,
        axe_dimensions=COMMON_OVERLAY_DIMENSIONS + DOOH_SPECIFIC_OVERLAY + COMMON_AXE_DIMENSIONS + DOOH_SPECIFIC_AXE,
    ),
}


def get_channel_capabilities(channel: Channel) -> TargetingCapabilities:
    """Get targeting capabilities for a specific channel."""
    return CHANNEL_CAPABILITIES[channel]


def get_overlay_dimensions(channel: Channel) -> list[TargetingDimension]:
    """Get available overlay targeting dimensions for a channel."""
    return CHANNEL_CAPABILITIES[channel].overlay_dimensions


def get_axe_dimensions(channel: Channel) -> list[TargetingDimension]:
    """Get AXE signal dimensions for a channel (include/exclude/macros only)."""
    return CHANNEL_CAPABILITIES[channel].axe_dimensions


def get_supported_channels() -> list[Channel]:
    """Get list of supported channels."""
    return list(Channel)


def is_dimension_supported(channel: Channel, dimension_key: str, for_overlay: bool = True) -> bool:
    """Check if a dimension is supported for a channel."""
    caps = CHANNEL_CAPABILITIES[channel]
    dimensions = caps.overlay_dimensions if for_overlay else caps.axe_dimensions
    return any(d.key == dimension_key for d in dimensions)
