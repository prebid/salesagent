"""Domain step definitions for UC-005: Discover Creative Formats.

These steps bridge BDD scenarios to the CreativeFormatsEnv test harness,
translating Gherkin context (ctx dicts with string/dict data) into real
adcp Format objects and harness calls.

The generic steps store raw format dicts in ctx["registry_formats"].
This module provides:
  - Helpers to convert those dicts to real Format objects
  - The creative_formats_env fixture lifecycle
  - Harness-aware overrides for When/Then steps
"""

from __future__ import annotations

from typing import Any

from adcp.types.generated_poc.core.format import (
    Assets,
    Assets5,
    Assets6,
    Assets7,
    Assets8,
    Assets9,
    Dimensions,
    Renders,
    Responsive,
)

# Map asset_type string → concrete Assets subclass
_ASSET_CLASS_MAP = {
    "image": Assets,
    "video": Assets5,
    "audio": Assets6,
    "text": Assets7,
    "markdown": Assets8,
    "html": Assets9,
}
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest

AGENT_URL = "https://creative.adcontextprotocol.org"

# ── FormatCategory mapping ──────────────────────────────────────────

_CATEGORY_MAP: dict[str, FormatCategory] = {
    "display": FormatCategory.display,
    "video": FormatCategory.video,
    "audio": FormatCategory.audio,
    "native": FormatCategory.native,
    "dooh": FormatCategory.dooh,
}

# ── Dict-to-Format conversion ──────────────────────────────────────


def _dict_to_format(d: dict[str, Any], index: int = 0) -> Format:
    """Convert a raw dict (from ctx["registry_formats"]) to a real Format object.

    This bridges the gap between the generic Given steps (which store dicts)
    and the harness (which expects Format objects).
    """
    name = d.get("name", f"format_{index}")
    fmt_type_str = d.get("type", "display")
    fmt_type = _CATEGORY_MAP.get(fmt_type_str, FormatCategory.display)

    # Build format_id
    fid_raw = d.get("format_id")
    if isinstance(fid_raw, dict):
        format_id = FormatId(
            agent_url=fid_raw.get("agent_url", AGENT_URL),
            id=fid_raw.get("id", f"fmt_{index}"),
        )
    else:
        format_id = FormatId(agent_url=AGENT_URL, id=f"fmt_{index}")

    kwargs: dict[str, Any] = {
        "format_id": format_id,
        "name": name,
        "type": fmt_type,
        "is_standard": True,
    }

    # Renders / dimensions
    renders_raw = d.get("renders")
    if renders_raw is not None:
        renders = []
        for r in renders_raw:
            dims_kwargs: dict[str, Any] = {}
            if "width" in r:
                dims_kwargs["width"] = r["width"]
            if "height" in r:
                dims_kwargs["height"] = r["height"]
            renders.append(Renders(role="primary", dimensions=Dimensions(**dims_kwargs) if dims_kwargs else None))
        if renders:  # empty renders_raw [] means "no dimension info" — omit field
            kwargs["renders"] = renders
    elif d.get("responsive") is True:
        kwargs["renders"] = [
            Renders(
                role="primary",
                dimensions=Dimensions(
                    min_width=300,
                    max_width=970,
                    responsive=Responsive(width=True, height=False),
                ),
            )
        ]
    elif d.get("responsive") is False:
        kwargs["renders"] = [
            Renders(
                role="primary",
                dimensions=Dimensions(width=728, height=90),
            )
        ]

    # Assets — from individual assets and/or asset_groups
    all_asset_types: list[str] = []
    assets_raw = d.get("assets")
    if assets_raw is not None:
        for a in assets_raw:
            all_asset_types.append(a.get("type", "image"))

    # Convert asset_groups to individual assets (production code treats
    # group types the same as individual asset types for filtering)
    asset_groups_raw = d.get("asset_groups")
    if asset_groups_raw is not None:
        for g in asset_groups_raw:
            for t in g.get("types", []):
                if t not in all_asset_types:
                    all_asset_types.append(t)

    if all_asset_types:
        assets = []
        for asset_type in all_asset_types:
            cls = _ASSET_CLASS_MAP.get(asset_type, Assets)
            assets.append(cls(asset_id=f"{asset_type}_asset", required=True))
        kwargs["assets"] = assets

    # Supported disclosure positions
    disclosure = d.get("supported_disclosure_positions")
    if disclosure is not None:
        kwargs["supported_disclosure_positions"] = disclosure

    # Output format IDs
    output_ids_raw = d.get("output_format_ids")
    if output_ids_raw is not None:
        kwargs["output_format_ids"] = [FormatId(agent_url=fid["agent_url"], id=fid["id"]) for fid in output_ids_raw]

    # Input format IDs
    input_ids_raw = d.get("input_format_ids")
    if input_ids_raw is not None:
        kwargs["input_format_ids"] = [FormatId(agent_url=fid["agent_url"], id=fid["id"]) for fid in input_ids_raw]

    return Format(**kwargs)


def dicts_to_formats(dicts: list[dict[str, Any]]) -> list[Format]:
    """Convert a list of raw dicts to Format objects."""
    return [_dict_to_format(d, i) for i, d in enumerate(dicts)]


# ── Request building ──────────────────────────────────────────────


def build_request_from_filters(filters: dict[str, Any]) -> ListCreativeFormatsRequest | None:
    """Build a ListCreativeFormatsRequest from ctx["request_filters"].

    Returns None when filters are empty (no-filter scenario).
    Raises pydantic.ValidationError for invalid input (ext-b scenarios).
    """
    if not filters:
        return None

    # Map BDD filter names to ListCreativeFormatsRequest field names
    req_kwargs: dict[str, Any] = {}

    if "type" in filters:
        req_kwargs["type"] = filters["type"]
    if "asset_types" in filters:
        req_kwargs["asset_types"] = filters["asset_types"]
    if "format_ids" in filters:
        req_kwargs["format_ids"] = [
            FormatId(agent_url=AGENT_URL, id=fid) if isinstance(fid, str) else fid for fid in filters["format_ids"]
        ]
    if "min_width" in filters:
        req_kwargs["min_width"] = filters["min_width"]
    if "max_width" in filters:
        req_kwargs["max_width"] = filters["max_width"]
    if "min_height" in filters:
        req_kwargs["min_height"] = filters["min_height"]
    if "max_height" in filters:
        req_kwargs["max_height"] = filters["max_height"]
    if "is_responsive" in filters:
        req_kwargs["is_responsive"] = filters["is_responsive"]
    if "name_search" in filters:
        req_kwargs["name_search"] = filters["name_search"]
    if "disclosure_positions" in filters:
        req_kwargs["disclosure_positions"] = filters["disclosure_positions"]
    if "output_format_ids" in filters:
        raw = filters["output_format_ids"]
        req_kwargs["output_format_ids"] = [
            FormatId(agent_url=fid["agent_url"], id=fid["id"]) if isinstance(fid, dict) else fid for fid in raw
        ]
    if "input_format_ids" in filters:
        raw = filters["input_format_ids"]
        req_kwargs["input_format_ids"] = [
            FormatId(agent_url=fid["agent_url"], id=fid["id"]) if isinstance(fid, dict) else fid for fid in raw
        ]

    if not req_kwargs:
        return None

    return ListCreativeFormatsRequest(**req_kwargs)


# ── Response normalization ──────────────────────────────────────────


def normalize_response_to_ctx(ctx: dict[str, Any]) -> None:
    """Normalize harness response into ctx format for Then step assertions.

    Converts the ListCreativeFormatsResponse payload into the dict-based
    format that Then steps expect, while also keeping the raw response.
    """
    response = ctx.get("harness_response")
    if response is None:
        return

    # If it's a TransportResult from call_via
    if hasattr(response, "is_success"):
        if response.is_error:
            error = response.error
            ctx["error"] = _exception_to_error_dict(error)
            return

        payload = response.payload
    else:
        payload = response

    # Convert to the dict format that Then steps expect
    formats_list = []
    for fmt in payload.formats:
        fmt_dict: dict[str, Any] = {"name": fmt.name}
        if fmt.type is not None:
            fmt_dict["type"] = fmt.type.value if hasattr(fmt.type, "value") else str(fmt.type)
        if fmt.format_id is not None:
            fmt_dict["format_id"] = {
                "agent_url": str(fmt.format_id.agent_url),
                "id": fmt.format_id.id,
            }
        if fmt.assets is not None:
            fmt_dict["assets"] = [{"type": getattr(a, "asset_type", getattr(a, "type", "unknown"))} for a in fmt.assets]
        if hasattr(fmt, "renders") and fmt.renders is not None:
            fmt_dict["renders"] = []
            for r in fmt.renders:
                render_dict: dict[str, Any] = {}
                if r.dimensions is not None:
                    if r.dimensions.width is not None:
                        render_dict["width"] = r.dimensions.width
                    if r.dimensions.height is not None:
                        render_dict["height"] = r.dimensions.height
                fmt_dict["renders"].append(render_dict)
        if hasattr(fmt, "supported_disclosure_positions") and fmt.supported_disclosure_positions is not None:
            fmt_dict["supported_disclosure_positions"] = fmt.supported_disclosure_positions
        if hasattr(fmt, "output_format_ids") and fmt.output_format_ids is not None:
            fmt_dict["output_format_ids"] = [
                {"agent_url": str(fid.agent_url), "id": fid.id} for fid in fmt.output_format_ids
            ]
        if hasattr(fmt, "input_format_ids") and fmt.input_format_ids is not None:
            fmt_dict["input_format_ids"] = [
                {"agent_url": str(fid.agent_url), "id": fid.id} for fid in fmt.input_format_ids
            ]

        formats_list.append(fmt_dict)

    ctx["result"] = {"formats": formats_list, "status": "completed"}

    # Also store the raw creative_agent_referrals if present
    if hasattr(payload, "creative_agents") and payload.creative_agents:
        ctx["creative_agent_referrals"] = [
            {
                "agent_url": str(ca.agent_url) if hasattr(ca, "agent_url") else str(ca),
                "capabilities": getattr(ca, "capabilities", []),
            }
            for ca in payload.creative_agents
        ]


def _exception_to_error_dict(exc: Exception) -> dict[str, str]:
    """Convert an exception from the harness into the error dict that Then steps expect."""
    from src.core.exceptions import AdCPError

    if isinstance(exc, AdCPError):
        return {
            "code": getattr(exc, "error_code", "UNKNOWN"),
            "message": str(exc),
            "suggestion": getattr(exc, "suggestion", "") or "",
        }

    # Pydantic ValidationError — extract field-level details
    if hasattr(exc, "errors"):
        errors = exc.errors()  # type: ignore[union-attr]
        fields = [".".join(str(loc) for loc in e.get("loc", ())) for e in errors]
        field_str = ", ".join(fields) if fields else "request"
        return {
            "code": "VALIDATION_ERROR",
            "message": f"Invalid parameter: {field_str} — {errors[0].get('msg', str(exc))}",
            "suggestion": f"Check the valid values for {field_str}",
        }

    return {
        "code": type(exc).__name__,
        "message": str(exc),
        "suggestion": "",
    }
