"""Shared AdCP reference-agent canonical creative format IDs."""

from __future__ import annotations

from typing import Any

DEFAULT_CREATIVE_AGENT_URL = "https://creative.adcontextprotocol.org"

CANONICAL_DISPLAY_FORMAT_IDS = ("display_image", "display_html", "display_js")
CANONICAL_CAROUSEL_FORMAT_IDS = (
    "product_carousel_display",
    "image_slideshow_5s_each",
    "mobile_story_vertical",
    "video_playlist_6s_bumpers",
)
CANONICAL_VIDEO_FORMAT_IDS = ("video_standard", "video_vast")
CANONICAL_AUDIO_FORMAT_IDS = ("audio_vast", "audio_15s", "audio_30s", "audio_60s")
CANONICAL_NATIVE_FORMAT_IDS = ("native_standard",)

CANONICAL_FORMAT_IDS = frozenset(
    CANONICAL_DISPLAY_FORMAT_IDS
    + CANONICAL_CAROUSEL_FORMAT_IDS
    + CANONICAL_VIDEO_FORMAT_IDS
    + CANONICAL_AUDIO_FORMAT_IDS
    + CANONICAL_NATIVE_FORMAT_IDS
)

DISPLAY_FORMAT_LABELS = {
    "display_image": "image",
    "display_html": "HTML5",
    "display_js": "JS",
}


def canonical_format_ref(format_id: str, **params: Any) -> dict[str, Any]:
    """Return a structured FormatId reference for the standard creative agent."""
    ref: dict[str, Any] = {
        "agent_url": DEFAULT_CREATIVE_AGENT_URL,
        "id": format_id,
    }
    ref.update({key: value for key, value in params.items() if value is not None})
    return ref


__all__ = [
    "CANONICAL_AUDIO_FORMAT_IDS",
    "CANONICAL_CAROUSEL_FORMAT_IDS",
    "CANONICAL_DISPLAY_FORMAT_IDS",
    "CANONICAL_FORMAT_IDS",
    "CANONICAL_NATIVE_FORMAT_IDS",
    "CANONICAL_VIDEO_FORMAT_IDS",
    "DEFAULT_CREATIVE_AGENT_URL",
    "DISPLAY_FORMAT_LABELS",
    "canonical_format_ref",
]
