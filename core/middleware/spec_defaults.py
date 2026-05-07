"""Apply AdCP spec-mandated server-side defaults to inbound requests.

Some 4.4+ schemas mark fields as ``required`` at the wire level even though
the spec text instructs sellers to apply a default for missing values from
pre-v3 clients. The most prominent example is ``GetProductsRequest.buying_mode``
— required in the JSON Schema, but the description says: *"Sellers receiving
requests from pre-v3 clients without buying_mode SHOULD default to 'brief'."*

The SDK's typed dispatcher validates request payloads against the library
Pydantic models *before* invoking the platform handler, so a per-handler
``model_validator`` cannot apply the default in time. This middleware fixes
that gap by intercepting MCP ``tools/call`` JSON-RPC bodies (and the matching
A2A skill payloads) and backfilling spec defaults before the SDK validator
runs.

Surface area is intentionally narrow: only the specific fields the spec
text calls out as defaultable. Adding new defaults must reference the spec
description that justifies them.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


_GET_PRODUCTS_DEFAULTS: dict[str, str] = {
    # GetProductsRequest.buying_mode — spec: "Sellers receiving requests from
    # pre-v3 clients without buying_mode SHOULD default to 'brief'."
    "buying_mode": "brief",
}

# Tools where adcp 4.4 added required ``account`` and ``idempotency_key``
# fields, but our impls resolve identity from the auth chain
# (``ResolvedIdentity`` produced by ``BearerTokenAuthMiddleware``) and dedupe
# at the DB layer regardless of caller key. Backfill placeholders at the
# wire boundary so the SDK's typed-dispatcher validation passes; our impl
# layer ignores the placeholders.
_AUTH_FILLED_TOOLS: frozenset[str] = frozenset(
    {"sync_creatives", "sync_accounts", "activate_signal", "create_media_buy"}
)

#: Sentinel ``AccountReference`` used to satisfy strict request validation
#: when callers don't supply one. ``account_id="auth-chain"`` signals that the
#: real identity lives on ``scope.state`` from BearerTokenAuthMiddleware.
_AUTH_CHAIN_ACCOUNT_REF = {"account_id": "auth-chain"}


#: Asset key names that map to a known ``asset_type`` literal. When a buyer
#: sends ``{"image": {...}}`` without an explicit ``asset_type`` field, the
#: SDK's typed dispatcher rejects the payload because the
#: ``AssetVariant`` discriminated union can't pick a branch. Mirror the
#: schema-side inference (``src.core.schemas._asset_type_compat``) at the
#: wire boundary so adcp-4.4 strictness doesn't break pre-v3 callers.
_KNOWN_ASSET_TYPES = frozenset(
    {
        "image",
        "video",
        "audio",
        "vast",
        "text",
        "url",
        "html",
        "javascript",
        "webhook",
        "css",
        "daast",
        "markdown",
        "brief",
        "catalog",
    }
)


def _infer_asset_type(key: str, value: dict[str, Any]) -> str | None:
    if "asset_type" in value:
        return None
    if key in _KNOWN_ASSET_TYPES:
        return key
    has_content = "content" in value
    has_url = "url" in value
    has_dims = "width" in value and "height" in value
    if has_content and not has_url:
        return "text"
    if has_url and has_dims:
        # Image assets require url + width + height. Only confidently
        # infer ``image`` when all three are present.
        return "image"
    if has_url:
        # ``url`` asset only requires ``url`` — safer default when caller
        # supplied just a URL with no dimensions.
        return "url"
    return None


def _apply_get_products_defaults(args: dict[str, Any]) -> None:
    for field, default in _GET_PRODUCTS_DEFAULTS.items():
        args.setdefault(field, default)


def _apply_auth_filled_defaults(args: dict[str, Any]) -> None:
    args.setdefault("account", _AUTH_CHAIN_ACCOUNT_REF)
    args.setdefault("idempotency_key", f"idem-{uuid.uuid4()}")


_DEFAULT_FORMAT_AGENT_URL = "https://creative.adcontextprotocol.org/"


def _normalise_format_id(creative: dict[str, Any]) -> None:
    """Wrap a string ``format_id`` as a FormatReferenceStructuredObject.

    adcp 4.4 made ``format_id`` a structured ``{agent_url, id}`` reference.
    Pre-4.4 buyer payloads pass a bare string — wrap so SDK validation
    picks the correct union arm.
    """
    fid = creative.get("format_id")
    if isinstance(fid, str):
        creative["format_id"] = {
            "agent_url": _DEFAULT_FORMAT_AGENT_URL,
            "id": fid,
        }


def _backfill_brief_name(value: dict[str, Any]) -> None:
    """Default brief.name to ``"brief"`` when missing.

    adcp 4.4 made ``name`` required on brief assets. Pre-v3 buyers send the
    brief without a name; default it so the discriminated-union validator
    accepts the payload.
    """
    if value.get("asset_type") != "brief":
        return
    if not value.get("name"):
        value["name"] = "brief"


def _demote_image_without_dims(value: dict[str, Any]) -> None:
    """If asset_type is ``image``/``video`` but width/height are missing, fall
    back to ``url`` — the URL-only variant has no dim requirement and accepts
    the same payload.

    Pre-4.4 buyer payloads frequently declare ``asset_type="image"`` (or
    ``video``) for any media URL without supplying dimensions; the strict
    4.4 variants reject them.
    """
    if value.get("asset_type") not in ("image", "video"):
        return
    if "width" in value and "height" in value:
        return
    if "url" in value:
        value["asset_type"] = "url"


def _normalise_assignments(arguments: dict[str, Any]) -> None:
    """Convert legacy ``{creative_id: [package_ids]}`` to the spec list form.

    adcp 3.12 requires ``assignments`` to be ``list[Assignment]`` with one
    ``{creative_id, package_id}`` entry per pair. Many existing buyers and
    tests still send the dict-of-lists form — expand it before the framework
    runs schema validation against the spec shape.
    """
    assignments = arguments.get("assignments")
    if not isinstance(assignments, dict):
        return
    expanded: list[dict[str, Any]] = []
    for creative_id, packages in assignments.items():
        if isinstance(packages, str):
            packages = [packages]
        if not isinstance(packages, list):
            continue
        for package_id in packages:
            expanded.append({"creative_id": creative_id, "package_id": package_id})
    arguments["assignments"] = expanded if expanded else None


def _backfill_asset_types(creatives: Any) -> None:
    """Inject missing ``asset_type`` on every asset value in ``creatives[*].assets``.

    Mutates the list in place. Called for ``sync_creatives`` so the SDK
    typed-dispatcher's discriminator can pick a branch on payloads minted
    with the pre-4.4 shape. Also normalises ``format_id`` from string to
    FormatReferenceStructuredObject and demotes image→url when dims are
    missing.
    """
    if not isinstance(creatives, list):
        return
    for creative in creatives:
        if not isinstance(creative, dict):
            continue
        _normalise_format_id(creative)
        assets = creative.get("assets")
        if not isinstance(assets, dict):
            continue
        for key, value in assets.items():
            if not isinstance(value, dict):
                continue
            inferred = _infer_asset_type(key, value)
            if inferred is not None:
                value["asset_type"] = inferred
            _demote_image_without_dims(value)
            _backfill_brief_name(value)


def _patch_mcp_tools_call(payload: dict[str, Any]) -> dict[str, Any]:
    """Patch a JSON-RPC ``tools/call`` body in place."""
    if payload.get("method") != "tools/call":
        return payload
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return payload
    name = params.get("name")
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return payload
    if name == "get_products":
        _apply_get_products_defaults(arguments)
    elif name in _AUTH_FILLED_TOOLS:
        _apply_auth_filled_defaults(arguments)
    if name == "sync_creatives":
        _backfill_asset_types(arguments.get("creatives"))
        _normalise_assignments(arguments)
    return payload


def _patch_a2a_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Patch an A2A skill request body in place."""
    skill = payload.get("skill")
    params = payload.get("params") if isinstance(payload, dict) else None
    if not isinstance(params, dict):
        return payload
    if skill == "get_products":
        _apply_get_products_defaults(params)
    elif skill in _AUTH_FILLED_TOOLS:
        _apply_auth_filled_defaults(params)
    if skill == "sync_creatives":
        _backfill_asset_types(params.get("creatives"))
        _normalise_assignments(params)
    return payload


class SpecDefaultsMiddleware:
    """ASGI middleware applying spec-mandated request defaults.

    Intercepts HTTP request bodies on ``/mcp/*`` and ``/`` (the A2A surface),
    decodes the JSON, applies defaults, and re-encodes. Non-JSON bodies and
    non-target paths pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        body = await self._read_body(receive)
        patched = self._patch_body(scope, body)
        if patched is None:
            patched = body

        async def _replay() -> Message:
            return {"type": "http.request", "body": patched, "more_body": False}

        await self.app(scope, _replay, send)

    @staticmethod
    async def _read_body(receive: Receive) -> bytes:
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                # disconnect or unexpected — let downstream see it
                return b"".join(chunks)
            chunks.append(message.get("body") or b"")
            more = message.get("more_body", False)
        return b"".join(chunks)

    @staticmethod
    def _patch_body(scope: Scope, body: bytes) -> bytes | None:
        if not body:
            return None
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None

        path = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            payload = _patch_mcp_tools_call(payload)
        else:
            # A2A surface lives at host root; skip Flask admin paths.
            if path.startswith(("/admin", "/static", "/auth", "/tenant", "/api", "/login", "/logout")):
                return None
            payload = _patch_a2a_skill(payload)

        try:
            return json.dumps(payload).encode("utf-8")
        except (TypeError, ValueError):
            logger.warning("SpecDefaultsMiddleware: failed to re-encode patched payload — passing original through")
            return None
