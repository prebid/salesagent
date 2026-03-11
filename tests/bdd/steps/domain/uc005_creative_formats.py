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

from src.core.schemas import Format, FormatId

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
