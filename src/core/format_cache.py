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
import logging
import re
from pathlib import Path
from typing import Any

from adcp.types import FormatId as LibraryFormatId

from src.core._deprecations import LEGACY_FORMAT_ID_SUNSET, warn_deprecated
from src.core.canonical_formats import CANONICAL_FORMAT_IDS, DEFAULT_CREATIVE_AGENT_URL
from src.core.schemas import FormatId

# Default agent URL for AdCP reference implementation
DEFAULT_AGENT_URL = DEFAULT_CREATIVE_AGENT_URL

# Cache file location
CACHE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "creative_formats"
CACHE_FILE = CACHE_DIR / "reference_formats.json"


def _is_default_agent_url(agent_url: Any) -> bool:
    normalized = str(agent_url).rstrip("/")
    if normalized.endswith("/mcp"):
        normalized = normalized.removesuffix("/mcp")
    return normalized == DEFAULT_AGENT_URL.rstrip("/")


def _canonical_reference_format_kwargs(format_id: str, agent_url: Any = DEFAULT_AGENT_URL) -> dict[str, Any]:
    """Map legacy reference-agent IDs to canonical FormatId kwargs.

    The public catalog only advertises canonical IDs, but older clients and
    persisted rows may still carry fixed-size reference-agent IDs such as
    ``display_300x250``. Internally those should behave like the parameterized
    canonical format ``display_image`` with width/height.
    """
    kwargs: dict[str, Any] = {"agent_url": agent_url, "id": format_id}
    if not _is_default_agent_url(agent_url):
        return kwargs

    display_match = re.fullmatch(r"display_(\d+)x(\d+)(?:_(image|html|js))?", format_id)
    if display_match:
        width, height, render_type = display_match.groups()
        canonical_id = {
            "html": "display_html",
            "js": "display_js",
        }.get(render_type or "image", "display_image")
        return {
            "agent_url": agent_url,
            "id": canonical_id,
            "width": int(width),
            "height": int(height),
        }

    video_match = re.fullmatch(r"video_(\d+)x(\d+)(?:_video)?", format_id)
    if video_match:
        width, height = video_match.groups()
        return {
            "agent_url": agent_url,
            "id": "video_standard",
            "width": int(width),
            "height": int(height),
        }

    if format_id == "native_1x1":
        return {"agent_url": agent_url, "id": "native_standard"}

    return kwargs


def canonical_format_identity(format_ref: Any) -> tuple[str, str, int | None, int | None, int | None]:
    """Return a comparable canonical identity for a FormatId-like value."""
    fmt = upgrade_legacy_format_id(format_ref)
    agent_url = str(fmt.agent_url).rstrip("/")
    if agent_url.endswith("/mcp"):
        agent_url = agent_url.removesuffix("/mcp")
    return (
        agent_url,
        fmt.id,
        fmt.width,
        fmt.height,
        int(fmt.duration_ms) if fmt.duration_ms is not None else None,
    )


def canonical_format_matches(requested: Any, supported: Any) -> bool:
    """Return whether two FormatId-like values are compatible.

    A populated parameter on both sides must match. A missing parameter on either
    side means that side is less specific and can match the other, preserving
    compatibility between legacy fixed-size IDs and canonical parameterized IDs.
    """
    req_agent, req_id, req_width, req_height, req_duration = canonical_format_identity(requested)
    sup_agent, sup_id, sup_width, sup_height, sup_duration = canonical_format_identity(supported)
    if (req_agent, req_id) != (sup_agent, sup_id):
        return False

    for requested_value, supported_value in (
        (req_width, sup_width),
        (req_height, sup_height),
        (req_duration, sup_duration),
    ):
        if requested_value is not None and supported_value is not None and requested_value != supported_value:
            return False

    return True


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


def _format_id_object_to_canonical(format_id_value: LibraryFormatId) -> FormatId:
    kwargs = _canonical_reference_format_kwargs(format_id_value.id, format_id_value.agent_url)
    if format_id_value.width is not None:
        kwargs["width"] = format_id_value.width
    if format_id_value.height is not None:
        kwargs["height"] = format_id_value.height
    if format_id_value.duration_ms is not None:
        kwargs["duration_ms"] = format_id_value.duration_ms

    if (
        isinstance(format_id_value, FormatId)
        and kwargs["id"] == format_id_value.id
        and kwargs["agent_url"] == format_id_value.agent_url
        and kwargs.get("width") == format_id_value.width
        and kwargs.get("height") == format_id_value.height
        and kwargs.get("duration_ms") == format_id_value.duration_ms
    ):
        return format_id_value

    return FormatId(**kwargs)


def upgrade_legacy_format_id(format_id_value: str | dict | FormatId) -> FormatId:
    """Upgrade legacy string format_id to namespaced FormatId object.

    If format_id is already an object, returns it as-is.
    If format_id is a string, looks up agent_url from cache or uses default.

    Args:
        format_id_value: Legacy string or new FormatId object

    Returns:
        FormatId object with agent_url namespace

    Examples:
        >>> upgrade_legacy_format_id("display_image")
        FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_image")

        >>> upgrade_legacy_format_id({"agent_url": "...", "id": "..."})
        FormatId(agent_url="...", id="...")
    """
    # FormatId object (including our subclass) - convert to canonical shape so
    # persisted legacy IDs like display_300x250 still behave as display_image
    # with width/height parameters.
    if isinstance(format_id_value, LibraryFormatId):
        return _format_id_object_to_canonical(format_id_value)

    # Already a dict with agent_url
    if isinstance(format_id_value, dict):
        raw_id = format_id_value.get("id", format_id_value.get("format_id"))
        if "agent_url" in format_id_value and raw_id is not None:
            kwargs = _canonical_reference_format_kwargs(str(raw_id), format_id_value["agent_url"])
            for key in ("width", "height", "duration_ms"):
                if format_id_value.get(key) is not None:
                    kwargs[key] = format_id_value[key]
            return FormatId(**kwargs)
        # Dict without agent_url - use default
        if raw_id is not None:
            return FormatId(**_canonical_reference_format_kwargs(str(raw_id)))

    # Legacy string format - upgrade to namespaced format (DEPRECATED)
    if isinstance(format_id_value, str):
        # Check cache for agent_url
        cache = load_format_cache()

        canonical_kwargs = _canonical_reference_format_kwargs(format_id_value)
        is_known_legacy_pattern = canonical_kwargs["id"] != format_id_value

        if format_id_value not in cache and not is_known_legacy_pattern:
            # Unknown format - fail loudly per AdCP spec guidance
            raise ValueError(
                f"Unknown format_id '{format_id_value}'. String format_ids are deprecated. "
                f"Must provide structured format with agent_url. "
                f"Known formats: {list(cache.keys())[:10]}..."
            )

        agent_url = cache.get(format_id_value, DEFAULT_AGENT_URL)
        canonical_kwargs = _canonical_reference_format_kwargs(format_id_value, agent_url)

        # DeprecationWarning surfaces in caller's test runner / log stream
        # (audience: buyer); log line surfaces in our server logs (audience:
        # operators). Different audiences, both worth keeping.
        suggested = {"agent_url": str(canonical_kwargs["agent_url"]), "id": canonical_kwargs["id"]}
        for key in ("width", "height", "duration_ms"):
            if canonical_kwargs.get(key) is not None:
                suggested[key] = canonical_kwargs[key]
        warn_deprecated(
            f"String format_id '{format_id_value}' is deprecated; send the structured shape "
            f"{suggested}. "
            f"String format_ids will be removed in {LEGACY_FORMAT_ID_SUNSET}."
        )
        logging.getLogger(__name__).warning(
            f"DEPRECATED string format_id '{format_id_value}' (sunset {LEGACY_FORMAT_ID_SUNSET}); "
            f"buyer should send {suggested}"
        )

        return FormatId(**canonical_kwargs)

    raise ValueError(f"Invalid format_id type: {type(format_id_value)}")


def get_agent_url_for_format(format_id: str) -> str:
    """Get agent_url for a given format ID string.

    Args:
        format_id: Format ID string (e.g., "display_image")

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

    # Canonical reference-agent formats, plus legacy string IDs retained only
    # so older persisted products can still be upgraded with a deprecation warning.
    default_formats = dict.fromkeys(sorted(CANONICAL_FORMAT_IDS), DEFAULT_AGENT_URL)
    default_formats.update(
        {
            # Legacy display formats
            "display_300x250": DEFAULT_AGENT_URL,
            "display_728x90": DEFAULT_AGENT_URL,
            "display_160x600": DEFAULT_AGENT_URL,
            "display_300x600": DEFAULT_AGENT_URL,
            "display_320x50": DEFAULT_AGENT_URL,
            "display_970x250": DEFAULT_AGENT_URL,
            # Legacy video formats
            "video_640x480": DEFAULT_AGENT_URL,
            "video_1280x720": DEFAULT_AGENT_URL,
            "video_1920x1080": DEFAULT_AGENT_URL,
            # Legacy native format
            "native_1x1": DEFAULT_AGENT_URL,
        }
    )

    save_format_cache(default_formats)


# Initialize on import
_initialize_default_cache()
