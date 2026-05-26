"""Shared helpers for declaring static VAST creative formats.

Adapters that deliver video or audio through VAST tag forwarding (FreeWheel,
SpringServe, and any future SSAI-based adapter) declare canonical reference
agent Format dicts with adapter-specific parameter constraints. They all share the same
asset shape (one ``vast_tag`` asset) and the same JSON envelope; extract
that shape here so each adapter just supplies its supported canonical IDs.
"""

from __future__ import annotations

from typing import Any

DurationVastSpec = tuple[str, str, int, str, str]

# Asset spec common to every VAST format declared via this module. The
# rendition dimensions and MIME types are carried at the Creative layer;
# the format itself just declares the slot for a VAST tag URL.
_VAST_TAG_ASSET: dict[str, Any] = {
    "item_type": "individual",
    "asset_id": "vast_tag",
    "asset_type": "vast",
    "required": True,
    "name": "VAST Tag URL",
}


def vast_format(
    format_id: str,
    name: str,
    description: str,
    agent_url: str,
    media_type: str = "video",
    format_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one AdCP Format dict for a VAST-delivered slot.

    ``media_type`` is "video" or "audio". The ``delivery`` envelope flags
    the format as VAST-delivered so callers downstream can route creative
    rendering correctly.
    """
    format_id_obj = {"id": format_id, "agent_url": agent_url}
    if format_params:
        format_id_obj.update({key: value for key, value in format_params.items() if value is not None})
    return {
        "format_id": format_id_obj,
        "name": name,
        "type": media_type,
        "description": description,
        "assets": [_VAST_TAG_ASSET],
        "delivery": {"vast": True},
    }


def duration_vast_formats(specs: list[DurationVastSpec], agent_url: str) -> list[dict[str, Any]]:
    """Build VAST formats from ``(format_id, media_type, duration_ms, name, description)`` specs."""
    return [
        vast_format(
            format_id,
            name,
            description,
            agent_url,
            media_type=media_type,
            format_params={"duration_ms": duration_ms},
        )
        for format_id, media_type, duration_ms, name, description in specs
    ]
