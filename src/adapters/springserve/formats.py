"""Static creative format declarations for the SpringServe adapter.

SpringServe delivers video through VAST tag forwarding or hosted creatives
(POST /api/v0/videos with a remote URL or multipart MP4 upload, max 500 MB).
For audio inventory, the live Talpa account accepts the passthrough VAST
``demand_class=tag`` path; hosted raw audio upload through /videos is not
advertised.

SpringServe should not mint SpringServe-owned AdCP format IDs for standard
VAST video or audio. It advertises canonical reference-agent formats with the
duration buckets it supports. Slot position is inventory/product targeting,
not creative format identity.

Audio support is a first-class concern, not a sidecar: SpringServe's Magnite x
iHeartMedia marketplace runs audio on the demand-tag API surface, with the
buyer-supplied audio VAST URL written as ``vast_endpoint_url`` on the demand
tag.
"""

from __future__ import annotations

from typing import Any

from src.adapters._format_helpers import DurationVastSpec, duration_vast_formats
from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL


def springserve_creative_formats(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Return the SpringServe adapter's supported creative formats.

    ``tenant_id`` is accepted for the adapter interface but standard formats
    are owned by the AdCP reference creative agent, not by the tenant.
    """
    specs: list[DurationVastSpec] = [
        ("video_vast", "video", 15000, "VAST Video 15s", "15-second VAST video creative."),
        ("video_vast", "video", 30000, "VAST Video 30s", "30-second VAST video creative."),
        ("audio_vast", "audio", 15000, "VAST Audio 15s", "15-second VAST audio creative."),
        ("audio_vast", "audio", 30000, "VAST Audio 30s", "30-second VAST audio creative."),
    ]
    return duration_vast_formats(specs, DEFAULT_CREATIVE_AGENT_URL)
