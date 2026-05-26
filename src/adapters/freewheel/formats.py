"""Static creative format declarations for the FreeWheel adapter.

FreeWheel delivers video through VAST tag forwarding: the publisher hosts ad
slots; buyers provide VAST tag URLs via creative_resources; the ad server
resolves them at delivery time.

FreeWheel should not mint FreeWheel-owned AdCP format IDs for standard VAST
video. It advertises the canonical reference-agent ``video_vast`` format with
the duration buckets it supports. Slot position is inventory/product targeting,
not creative format identity.
"""

from __future__ import annotations

from typing import Any

from src.adapters._format_helpers import DurationVastSpec, duration_vast_formats
from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL


def freewheel_creative_formats(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Return the FreeWheel adapter's supported creative formats.

    ``tenant_id`` is accepted for the adapter interface but standard formats
    are owned by the AdCP reference creative agent, not by the tenant.
    """
    specs: list[DurationVastSpec] = [
        ("video_vast", "video", 15000, "VAST Video 15s", "15-second VAST video creative."),
        ("video_vast", "video", 30000, "VAST Video 30s", "30-second VAST video creative."),
    ]
    return duration_vast_formats(specs, DEFAULT_CREATIVE_AGENT_URL)
