"""Starlette middleware for REST AdCP backward-compatibility normalization.

Normalizes deprecated field names in JSON request bodies for /api/v1/
endpoints before FastAPI's Pydantic model parsing strips unknown fields.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from src.core.request_compat import normalize_request_params

logger = logging.getLogger(__name__)

# Map URL path suffixes to tool names for normalization.
_PATH_TO_TOOL: dict[str, str] = {
    "/products": "get_products",
    "/media-buys": "create_media_buy",
    "/creatives/sync": "sync_creatives",
}


class RestCompatMiddleware(BaseHTTPMiddleware):
    """Normalize deprecated fields in REST JSON bodies.

    Intercepts POST requests to /api/v1/* endpoints, normalizes the JSON
    body using the shared normalizer, and replaces the request body so
    Pydantic models see current-version field names.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method != "POST" or not request.url.path.startswith("/api/v1/"):
            return await call_next(request)

        # Determine tool name from URL path
        tool_name = self._resolve_tool_name(request.url.path)
        if not tool_name:
            return await call_next(request)

        content_type = request.headers.get("content-type", "")
        if "json" not in content_type:
            return await call_next(request)

        try:
            raw_body = await request.body()
            if not raw_body:
                return await call_next(request)

            body_dict: dict[str, Any] = json.loads(raw_body)
            result = normalize_request_params(tool_name, body_dict)

            if result.translations_applied:
                # Replace the request body with normalized JSON
                normalized_bytes = json.dumps(result.params).encode("utf-8")
                request._body = normalized_bytes  # noqa: SLF001
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # Let FastAPI handle malformed JSON

        return await call_next(request)

    @staticmethod
    def _resolve_tool_name(path: str) -> str | None:
        """Map URL path to tool name for normalization."""
        # Strip /api/v1 prefix
        suffix = path.removeprefix("/api/v1")
        return _PATH_TO_TOOL.get(suffix)
