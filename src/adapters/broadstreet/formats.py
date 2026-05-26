"""Canonical creative formats supported by the Broadstreet adapter."""

from __future__ import annotations

from typing import Any

from src.core.schemas import Format
from src.core.standard_formats import get_standard_format

BROADSTREET_TEMPLATE_CANONICAL_FORMAT_IDS = {
    "static": "display_image",
    "html": "display_html",
    "cube_3d": "image_slideshow_5s_each",
    "gallery": "image_slideshow_5s_each",
    "push_pin": "display_image",
    "native": "native_standard",
}

BROADSTREET_CANONICAL_FORMAT_IDS = (
    "display_image",
    "display_html",
    "display_js",
    "image_slideshow_5s_each",
    "native_standard",
)


def broadstreet_template_canonical_format_id(template_type: str) -> str | None:
    """Return the canonical reference-agent format ID for a Broadstreet template."""
    return BROADSTREET_TEMPLATE_CANONICAL_FORMAT_IDS.get(template_type)


def broadstreet_creative_format_models() -> list[Format]:
    """Return Broadstreet-supported canonical reference-agent formats."""
    formats: list[Format] = []
    for format_id in BROADSTREET_CANONICAL_FORMAT_IDS:
        fmt = get_standard_format(format_id)
        if fmt is not None:
            formats.append(fmt.model_copy(deep=True))
    return formats


def broadstreet_creative_formats() -> list[dict[str, Any]]:
    """Return Broadstreet-supported canonical formats as AdCP Format dicts."""
    return [fmt.model_dump(mode="json") for fmt in broadstreet_creative_format_models()]


__all__ = [
    "BROADSTREET_CANONICAL_FORMAT_IDS",
    "BROADSTREET_TEMPLATE_CANONICAL_FORMAT_IDS",
    "broadstreet_creative_format_models",
    "broadstreet_creative_formats",
    "broadstreet_template_canonical_format_id",
]
