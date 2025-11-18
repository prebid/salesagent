"""Format cache for backward compatibility with legacy string format_ids.

This module provides a mapping from legacy string format IDs to the new
AdCP v2.4 namespaced format_id objects. Formats are cached from the reference
creative agent implementation to ensure tests work offline.

Design principles:
1. Tests never depend on external infrastructure
2. Legacy string format_ids automatically upgrade to namespaced format
3. Cache is updated periodically but not required for operation
4. Default agent_url is the AdCP reference implementation
"""

import json
from pathlib import Path

from src.core.schemas import FormatId, url

# Default agent URL for AdCP reference implementation
DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

# Cache file location
CACHE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "creative_formats"
CACHE_FILE = CACHE_DIR / "reference_formats.json"


def load_format_cache() -> dict[str, str]:
    """Load cached formats from reference implementation.

    Returns:
        Dict mapping format_id (string) to agent_url
    """
    if not CACHE_FILE.exists():
        # Return empty cache - will use default agent URL
        return {}

    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
            return data.get("formats", {})
    except (OSError, json.JSONDecodeError):
        return {}


def save_format_cache(formats: dict[str, str]) -> None:
    """Save format cache to disk.

    Args:
        formats: Dict mapping format_id to agent_url
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    data = {
        "formats": formats,
        "cached_at": "2025-10-13T20:00:00Z",  # Will be updated dynamically
        "agent_url": DEFAULT_AGENT_URL,
    }

    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def upgrade_legacy_format_id(format_id_value: str | dict | FormatId) -> FormatId:
    """Upgrade legacy string format_id to namespaced FormatId object.

    If format_id is already an object, returns it as-is.
    If format_id is a string, looks up agent_url from cache or uses default.

    Args:
        format_id_value: Legacy string or new FormatId object

    Returns:
        FormatId object with agent_url namespace

    Examples:
        >>> upgrade_legacy_format_id("display_300x250")
        FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250")

        >>> upgrade_legacy_format_id({"agent_url": "...", "id": "..."})
        FormatId(agent_url="...", id="...")
    """
    # Already a FormatId object
    if isinstance(format_id_value, FormatId):
        return format_id_value

    # Already a dict with agent_url
    if isinstance(format_id_value, dict):
        if "agent_url" in format_id_value and "id" in format_id_value:
            return FormatId(**format_id_value)
        # Dict without agent_url - use default
        if "id" in format_id_value:
            return FormatId(agent_url=url(DEFAULT_AGENT_URL), id=format_id_value["id"])

    # Legacy string format - upgrade to namespaced format (DEPRECATED)
    if isinstance(format_id_value, str):
        import logging

        logger = logging.getLogger(__name__)

        # Check cache for agent_url
        cache = load_format_cache()

        if format_id_value not in cache:
            # Unknown format - fail loudly per AdCP spec guidance
            raise ValueError(
                f"Unknown format_id '{format_id_value}'. String format_ids are deprecated. "
                f"Must provide structured format with agent_url. "
                f"Known formats: {list(cache.keys())[:10]}..."
            )

        agent_url = cache[format_id_value]

        # Log deprecation warning
        logger.warning(
            f"⚠️  DEPRECATED: String format_id '{format_id_value}' received. "
            f"Use structured format: {{'agent_url': '{agent_url}', 'id': '{format_id_value}'}}. "
            f"String format_ids will be removed in a future version."
        )

        return FormatId(agent_url=url(agent_url), id=format_id_value)

    raise ValueError(f"Invalid format_id type: {type(format_id_value)}")


def get_agent_url_for_format(format_id: str) -> str:
    """Get agent_url for a given format ID string.

    Args:
        format_id: Format ID string (e.g., "display_300x250")

    Returns:
        Agent URL (from cache or default)
    """
    cache = load_format_cache()
    return cache.get(format_id, DEFAULT_AGENT_URL)


# Initialize cache with common formats if it doesn't exist
def _initialize_default_cache():
    """Initialize cache with common AdCP standard formats."""
    if CACHE_FILE.exists():
        return

    # Common IAB standard formats from AdCP reference implementation
    default_formats = {
        # Display formats
        "display_300x250": DEFAULT_AGENT_URL,
        "display_728x90": DEFAULT_AGENT_URL,
        "display_160x600": DEFAULT_AGENT_URL,
        "display_300x600": DEFAULT_AGENT_URL,
        "display_320x50": DEFAULT_AGENT_URL,
        "display_970x250": DEFAULT_AGENT_URL,
        # Video formats
        "video_640x480": DEFAULT_AGENT_URL,
        "video_1280x720": DEFAULT_AGENT_URL,
        "video_1920x1080": DEFAULT_AGENT_URL,
        # Audio formats
        "audio_30s": DEFAULT_AGENT_URL,
        "audio_60s": DEFAULT_AGENT_URL,
        # Native format
        "native_1x1": DEFAULT_AGENT_URL,
    }

    save_format_cache(default_formats)


# Initialize on import
_initialize_default_cache()
