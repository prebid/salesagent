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
from src.core.signing import operation_for_rest_route

logger = logging.getLogger(__name__)

#: The POST routes whose bodies get deprecated-field normalization.
#:
#: The KEYS are the gate and stay hand-listed on purpose: ``normalize_request_params``
#: applies its top-level translations for ALL tools, so there is no per-tool translation
#: registry to derive a wider set from — widening this would start normalizing
#: deprecated fields on 10 REST routes that do not get it today, a compat decision
#: rather than a refactor (#1291 B2 refinement R-M2; tracked as salesagent-i12h).
#:
#: The VALUES are derived from the ONE route registry
#: (``src/core/signing/operations.py``), so this table cannot drift from the route it
#: names; ``tests/unit/test_architecture_signing_operations.py::TestRestCompatTableAgreesWithTheRouteTable``
#: fails the build if it ever does.
_NORMALIZED_POST_PATHS: tuple[str, ...] = ("/products", "/media-buys", "/creatives/sync")

_PATH_TO_TOOL: dict[str, str] = {
    suffix: operation_for_rest_route("POST", f"/api/v1{suffix}") for suffix in _NORMALIZED_POST_PATHS
}

_UNROUTED_SUFFIXES = sorted(suffix for suffix, tool in _PATH_TO_TOOL.items() if not tool)
if _UNROUTED_SUFFIXES:  # pragma: no cover - import-time wiring check
    # A suffix that names no route would derive "", and an empty tool name makes
    # ``_resolve_tool_name`` fall through — normalization would silently stop for that
    # route while the drift guard stayed green (it compares two empty strings). Loud
    # at import instead.
    raise RuntimeError(f"rest-compat normalization targets paths with no POST /api/v1 route: {_UNROUTED_SUFFIXES}")


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

            # The wire bytes AS SENT — the idempotency payload-hash input.
            # Stashed before any rewrite (bytes are immutable, so downstream
            # mutation cannot corrupt the capture); read by api_v1's
            # _raw_json_body dependency.
            request.state.raw_wire_payload = raw_body

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
