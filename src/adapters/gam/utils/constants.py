"""
Constants and enums for Google Ad Manager adapter.

This module centralizes all GAM-specific constants including:
- API version information
- OAuth scopes
- Creative size limits
- Status enums
- Targeting types
- Configuration defaults
"""

from enum import Enum


class GAMCreativeType(Enum):
    """Creative types supported by GAM."""

    DISPLAY = "display"
    VIDEO = "video"
    NATIVE = "native"
    HTML5 = "html5"
    RICH_MEDIA = "rich_media"
    THIRD_PARTY_TAG = "third_party_tag"
    VAST = "vast"


class GAMOrderStatus(Enum):
    """GAM Order status values."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    DISAPPROVED = "DISAPPROVED"
    PAUSED = "PAUSED"
    CANCELED = "CANCELED"
    ARCHIVED = "ARCHIVED"


class GAMLineItemStatus(Enum):
    """GAM Line Item status values."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    DISAPPROVED = "DISAPPROVED"
    ARCHIVED = "ARCHIVED"


class GAMLineItemType(Enum):
    """GAM Line Item types."""

    SPONSORSHIP = "SPONSORSHIP"
    STANDARD = "STANDARD"
    NETWORK = "NETWORK"
    BULK = "BULK"
    PRICE_PRIORITY = "PRICE_PRIORITY"
    HOUSE = "HOUSE"
    LEGACY_DFP = "LEGACY_DFP"
    CLICK_TRACKING = "CLICK_TRACKING"
    ADSENSE = "ADSENSE"
    AD_EXCHANGE = "AD_EXCHANGE"
    BUMPER = "BUMPER"
    ADMOB = "ADMOB"
    PREFERRED_DEAL = "PREFERRED_DEAL"


class GAMCreativeStatus(Enum):
    """GAM Creative status values."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DELETED = "DELETED"


class GAMTargetingType(Enum):
    """Types of targeting available in GAM."""

    GEO = "geo"
    TECHNOLOGY = "technology"
    CUSTOM = "custom"
    DAYPART = "daypart"
    USER_DOMAIN = "user_domain"
    CONTENT = "content"
    VIDEO_POSITION = "video_position"
    MOBILE_APPLICATION = "mobile_application"


class GAMEnvironmentType(Enum):
    """GAM environment types for targeting."""

    BROWSER = "BROWSER"
    MOBILE_APP = "MOBILE_APP"


class GAMDeviceCategory(Enum):
    """Standard GAM device categories."""

    DESKTOP = "DESKTOP"
    SMARTPHONE = "SMARTPHONE"
    TABLET = "TABLET"
    CONNECTED_TV = "CONNECTED_TV"
    SET_TOP_BOX = "SET_TOP_BOX"


class GAMCompanyType(Enum):
    """GAM company types."""

    ADVERTISER = "ADVERTISER"
    AGENCY = "AGENCY"
    HOUSE_ADVERTISER = "HOUSE_ADVERTISER"
    HOUSE_AGENCY = "HOUSE_AGENCY"
    AD_NETWORK = "AD_NETWORK"
    PARTNER = "PARTNER"
    CHILD_PUBLISHER = "CHILD_PUBLISHER"
    VIEWABILITY_PROVIDER = "VIEWABILITY_PROVIDER"
    UNKNOWN = "UNKNOWN"


# API Configuration
GAM_SCOPES = ["https://www.googleapis.com/auth/dfp"]

# Creative size limits (in bytes)
GAM_CREATIVE_SIZE_LIMITS = {
    "display": 150_000,  # 150KB
    "video": 2_200_000,  # 2.2MB
    "rich_media": 2_200_000,  # 2.2MB
    "native": 150_000,  # 150KB
    "html5": None,  # Let GAM API handle validation
}

# Maximum creative dimensions (pixels)
GAM_MAX_DIMENSIONS = {
    "width": 1800,
    "height": 1500,
}

# Allowed file extensions by creative type
GAM_ALLOWED_EXTENSIONS = {
    "display": [".jpg", ".jpeg", ".png", ".gif", ".webp"],
    "video": [".mp4", ".webm", ".mov", ".avi"],
    "rich_media": [".swf", ".html", ".zip"],
    "html5": [".html", ".htm", ".html5", ".zip"],
    "native": [".jpg", ".jpeg", ".png", ".gif", ".webp"],
}

# Standard creative sizes (width x height)
GAM_STANDARD_SIZES = {
    # Display banners
    "leaderboard": (728, 90),
    "banner": (468, 60),
    "half_banner": (234, 60),
    "button_1": (120, 90),
    "button_2": (120, 60),
    "micro_bar": (88, 31),
    # Rectangles
    "medium_rectangle": (300, 250),
    "large_rectangle": (336, 280),
    "rectangle": (180, 150),
    "small_rectangle": (200, 200),
    "square": (250, 250),
    "small_square": (200, 200),
    # Skyscrapers
    "skyscraper": (120, 600),
    "wide_skyscraper": (160, 600),
    "half_page": (300, 600),
    "vertical_banner": (120, 240),
    # Mobile
    "mobile_banner": (320, 50),
    "mobile_leaderboard": (320, 100),
    "large_mobile_banner": (320, 100),
    # Video
    "video_player": (640, 480),
    "video_player_large": (853, 480),
    "video_player_hd": (1280, 720),
}

# Default configuration values
GAM_DEFAULT_CONFIG = {
    "timeout_seconds": 30,
    "retry_attempts": 3,
    "retry_delay_seconds": 1.0,
    "batch_size": 100,
    "enable_compression": True,
    "validate_only": False,
}

# Budget configuration
GAM_BUDGET_LIMITS = {
    "min_daily_budget": 1.00,  # $1 minimum
    "max_daily_budget": 1_000_000.00,  # $1M maximum
    "currency_precision": 2,  # 2 decimal places
    "micro_multiplier": 1_000_000,  # GAM uses micro amounts
}

# Targeting limits
GAM_TARGETING_LIMITS = {
    "max_geo_targets": 1000,
    "max_custom_targeting_keys": 50,
    "max_custom_targeting_values": 100,
    "max_ad_units": 500,
    "max_placements": 200,
}

# Name length limits
GAM_NAME_LIMITS = {
    "max_order_name_length": 255,
    "max_line_item_name_length": 255,
}

# Rate limiting
GAM_RATE_LIMITS = {
    "requests_per_second": 10,
    "requests_per_minute": 600,
    "requests_per_hour": 36000,
    "burst_limit": 20,
}

# Error retry configuration
GAM_RETRY_CONFIG = {
    "max_attempts": 3,
    "initial_delay": 1.0,
    "max_delay": 60.0,
    "exponential_base": 2.0,
    "jitter": True,
}

# Health check configuration
GAM_HEALTH_CHECK_CONFIG = {
    "timeout_seconds": 10,
    "max_test_ad_units": 5,
    "check_interval_minutes": 30,
    "failure_threshold": 3,
}

# Logging configuration
GAM_LOGGING_CONFIG = {
    "max_request_size": 1000,  # characters
    "max_response_size": 2000,  # characters
    "sensitive_fields": [
        "service_account_key_file",
        "access_token",
        "refresh_token",
        "api_key",
        "password",
        "secret",
    ],
    "correlation_id_header": "X-GAM-Correlation-ID",
}

# Video creative configuration
GAM_VIDEO_CONFIG = {
    "supported_formats": ["MP4", "WEBM", "MOV", "AVI"],
    "max_duration_seconds": 600,  # 10 minutes
    "min_duration_seconds": 1,
    "supported_codecs": ["H.264", "VP8", "VP9"],
    "max_bitrate_kbps": 10000,
    "supported_aspect_ratios": [
        (16, 9),  # Widescreen
        (4, 3),  # Standard
        (1, 1),  # Square
        (9, 16),  # Vertical/Mobile
    ],
}

# Native creative configuration
GAM_NATIVE_CONFIG = {
    "required_fields": ["headline", "body", "image"],
    "optional_fields": ["call_to_action", "advertiser", "star_rating", "price"],
    "max_headline_length": 50,
    "max_body_length": 200,
    "max_cta_length": 15,
    "image_requirements": {
        "min_width": 200,
        "min_height": 200,
        "aspect_ratio_tolerance": 0.1,
        "formats": ["JPG", "PNG", "GIF", "WEBP"],
    },
}

# Third-party tag configuration
GAM_THIRD_PARTY_CONFIG = {
    "allowed_snippet_types": ["html", "javascript", "vast_xml", "vast_url"],
    "max_snippet_size": 50000,  # 50KB
    "prohibited_functions": [
        "eval",
        "document.write",
        "document.writeln",
        "setTimeout",
        "setInterval",
        "Function",
    ],
    "required_https": True,
}

# Reporting configuration
GAM_REPORTING_CONFIG = {
    "max_report_rows": 100000,
    "default_timezone": "UTC",
    "supported_formats": ["CSV", "XML", "JSON"],
    "max_date_range_days": 366,
    "default_dimensions": ["DATE", "AD_UNIT_NAME"],
    "default_columns": ["IMPRESSIONS", "CLICKS", "CTR", "REVENUE"],
}

# Cache configuration
GAM_CACHE_CONFIG = {
    "inventory_ttl_hours": 24,
    "targeting_ttl_hours": 12,
    "company_ttl_hours": 168,  # 1 week
    "creative_ttl_hours": 6,
    "max_cache_size_mb": 100,
}
