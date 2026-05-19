"""Typed CRUD over the SpringServe Videos / Creatives API.

Endpoint reference:
- POST   /api/v0/videos
- GET    /api/v0/videos/{id}
- PUT    /api/v0/videos/{id}
- DELETE /api/v0/videos/{id}

Despite the path being ``/videos``, the endpoint hosts BOTH video and
audio creatives -- they're discriminated by ``creative_format`` ("video"
| "audio") and ``creative_content_type`` (e.g. ``video/mp4``,
``audio/mp4``, ``audio/mpeg``).

Two upload paths:

1. **Remote URL** (the path we use today): POST with
   ``creative_remote_url`` -- SpringServe pulls + transcodes the file
   from the supplied URL.
2. **Multipart upload** (≤ 500 MB): not implemented here; ``remote_url``
   covers our hosted-asset case.

Binding a creative to a demand tag is NOT part of this client -- that
edit goes on the demand tag itself via ``line_item_ratios``. The adapter's
``associate_creatives`` method composes those calls.
"""

from __future__ import annotations

from typing import Any

from src.adapters.springserve._transport import SpringServeTransport
from src.adapters.springserve.entities import VideoCreative


class SpringServeCreativesClient:
    """Video + Audio creative CRUD bound to one :class:`SpringServeTransport`."""

    def __init__(self, transport: SpringServeTransport):
        self._transport = transport

    def create(
        self,
        *,
        name: str,
        demand_partner_id: int,
        creative_remote_url: str,
        creative_format: str = "video",
        creative_content_type: str = "video/mp4",
        active: bool = True,
        duration_seconds: int | None = None,
        width: int | None = None,
        height: int | None = None,
        creative_landing_page_url: str | None = None,
        secondary_code: str | None = None,
        **extras: Any,
    ) -> VideoCreative:
        """POST a new video/audio creative and return the parsed entity.

        ``creative_format`` is "video" (default) or "audio". Audio creatives
        use ``audio/mpeg`` or ``audio/mp4`` for ``creative_content_type``;
        no separate audio endpoint -- it's the same ``/videos`` surface.
        """
        body: dict[str, Any] = {
            "name": name,
            "demand_partner_id": demand_partner_id,
            "creative_format": creative_format,
            "creative_content_type": creative_content_type,
            "creative_remote_url": creative_remote_url,
            "active": active,
        }
        if duration_seconds is not None:
            body["duration_seconds"] = duration_seconds
        if width is not None:
            body["width"] = width
        if height is not None:
            body["height"] = height
        if creative_landing_page_url is not None:
            body["creative_landing_page_url"] = creative_landing_page_url
        if secondary_code is not None:
            body["secondary_code"] = secondary_code
        body.update(extras)
        response = self._transport.post_json("/videos", body)
        return VideoCreative.model_validate(response)

    def get(self, video_id: int) -> VideoCreative:
        response = self._transport.get_json(f"/videos/{video_id}")
        return VideoCreative.model_validate(response)

    def update(self, video_id: int, **fields: Any) -> VideoCreative:
        """PUT changes to a creative. Common toggles: ``active``,
        ``creative_landing_page_url``, ``secondary_code``."""
        response = self._transport.put_json(f"/videos/{video_id}", fields)
        return VideoCreative.model_validate(response)

    def delete(self, video_id: int) -> None:
        self._transport.delete_json(f"/videos/{video_id}")
