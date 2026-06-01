#!/usr/bin/env python3
"""Generate .creative-agent-catalog.json matching Docker's creative agent.

This script produces a 50-format catalog whose format IDs are a subset of
what Docker's creative agent actually serves. The catalog is used by BDD
tests (via load_real_catalog()) so that harness-backed transports (impl,
a2a, mcp, rest) use realistic format definitions.

The total is capped at 50 to stay within the AdCP default pagination
page size (max_results=50), ensuring the "full catalog" scenario passes
without needing pagination handling in BDD steps.

Distribution: 30 display, 10 video, 3 native, 4 dooh, 3 audio = 50 total.

Usage:
    uv run python scripts/refresh-creative-catalog.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adcp.types.generated_poc.enums.format_category import FormatCategory

from tests.factories.format import (
    FormatFactory,
    FormatIdFactory,
    make_asset,
    make_fixed_renders,
    make_responsive_renders,
)

AGENT_URL = "https://creative.adcontextprotocol.org"


def _build_catalog() -> list[dict]:
    """Build the 57-format catalog matching Docker's creative agent."""
    formats = []

    # ── Display formats (30) ─────────────────────────────────────────
    # Sized image formats (7 sizes)
    _display_image_sizes = [
        ("display_160x600_image", "Wide Skyscraper - Image", 160, 600),
        ("display_300x250_image", "Medium Rectangle - Image", 300, 250),
        ("display_300x600_image", "Half Page - Image", 300, 600),
        ("display_320x50_image", "Mobile Banner - Image", 320, 50),
        ("display_336x280_image", "Large Rectangle - Image", 336, 280),
        ("display_728x90_image", "Leaderboard - Image", 728, 90),
        ("display_970x250_image", "Billboard - Image", 970, 250),
    ]
    for fmt_id, name, w, h in _display_image_sizes:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.display,
            assets=[make_asset("image"), make_asset("url", "click_url"), make_asset("url", "impression_tracker")],
            renders=[make_fixed_renders(width=w, height=h)],
        )
        formats.append(fmt)

    # Sized HTML5 formats (6 sizes)
    _display_html_sizes = [
        ("display_160x600_html", "Wide Skyscraper - HTML5", 160, 600),
        ("display_300x250_html", "Medium Rectangle - HTML5", 300, 250),
        ("display_300x600_html", "Half Page - HTML5", 300, 600),
        ("display_336x280_html", "Large Rectangle - HTML5", 336, 280),
        ("display_728x90_html", "Leaderboard - HTML5", 728, 90),
        ("display_970x250_html", "Billboard - HTML5", 970, 250),
    ]
    for fmt_id, name, w, h in _display_html_sizes:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.display,
            assets=[make_asset("html"), make_asset("url", "impression_tracker")],
            renders=[make_fixed_renders(width=w, height=h)],
        )
        formats.append(fmt)

    # Sized generative formats (7 sizes)
    _display_gen_sizes = [
        ("display_160x600_generative", "Wide Skyscraper - AI Generated", 160, 600),
        ("display_300x250_generative", "Medium Rectangle - AI Generated", 300, 250),
        ("display_300x600_generative", "Half Page - AI Generated", 300, 600),
        ("display_320x50_generative", "Mobile Banner - AI Generated", 320, 50),
        ("display_336x280_generative", "Large Rectangle - AI Generated", 336, 280),
        ("display_728x90_generative", "Leaderboard - AI Generated", 728, 90),
        ("display_970x250_generative", "Billboard - AI Generated", 970, 250),
    ]
    for fmt_id, name, w, h in _display_gen_sizes:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.display,
            assets=[make_asset("text", "generation_prompt"), make_asset("url", "impression_tracker")],
            renders=[make_fixed_renders(width=w, height=h)],
        )
        formats.append(fmt)

    # Responsive display (no fixed dimensions): image, html, js, generative
    _display_responsive = [
        ("display_generative", "Display Banner - AI Generated", ["text", "url"]),
        ("display_html", "Display Banner - HTML5", ["html", "url"]),
        ("display_image", "Display Banner - Image", ["image", "url", "url"]),
        ("display_js", "Display Banner - JavaScript", ["javascript", "url"]),
    ]
    for fmt_id, name, asset_types in _display_responsive:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        assets = [make_asset(at, f"{at}_asset_{i}") for i, at in enumerate(asset_types)]
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.display,
            assets=assets,
            renders=None,
        )
        formats.append(fmt)

    # Card formats — standard (fixed 300x400), detailed (responsive)
    _card_formats = [
        ("format_card_standard", "Format Card - Standard", 300, 400, ["text", "url"]),
        ("format_card_detailed", "Format Card - Detailed", None, None, ["text", "url"]),
        ("product_card_standard", "Product Card - Standard", 300, 400, ["image", "text", "url"]),
        ("product_card_detailed", "Product Card - Detailed", None, None, ["image", "text", "url"]),
        ("proposal_card_standard", "Proposal Card - Standard", 300, 400, ["text", "image", "url"]),
        ("proposal_card_detailed", "Proposal Card - Detailed", None, None, ["text", "image", "url"]),
    ]
    for fmt_id, name, w, h, asset_types in _card_formats:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        assets = [make_asset(at, f"{at}_asset_{i}") for i, at in enumerate(asset_types)]
        if w is None:
            renders = [make_responsive_renders()]
        else:
            renders = [make_fixed_renders(width=w, height=h)]
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.display,
            assets=assets,
            renders=renders,
        )
        formats.append(fmt)

    # ── Video formats (10) ───────────────────────────────────────────
    # Sized video formats
    _video_sized = [
        ("video_1080x1080", "Square Video - 1080x1080", 1080, 1080),
        ("video_1080x1920", "Vertical Video - 1080x1920", 1080, 1920),
        ("video_1280x720", "HD Video - 1280x720", 1280, 720),
        ("video_1920x1080", "Full HD Video - 1920x1080", 1920, 1080),
    ]
    for fmt_id, name, w, h in _video_sized:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.video,
            assets=[make_asset("video"), make_asset("url", "impression_tracker")],
            renders=[make_fixed_renders(width=w, height=h)],
        )
        formats.append(fmt)

    # Unsized video formats
    _video_unsized = [
        ("video_ctv_preroll_30s", "CTV Pre-Roll - 30 seconds"),
        ("video_standard", "Standard Video"),
        ("video_standard_15s", "Standard Video - 15 seconds"),
        ("video_standard_30s", "Standard Video - 30 seconds"),
    ]
    for fmt_id, name in _video_unsized:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.video,
            assets=[make_asset("video"), make_asset("url", "impression_tracker")],
            renders=None,
        )
        formats.append(fmt)

    # VAST video formats
    _video_vast = [
        ("video_vast", "VAST Video"),
        ("video_vast_30s", "VAST Video - 30 seconds"),
    ]
    for fmt_id, name in _video_vast:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.video,
            assets=[make_asset("vast")],
            renders=None,
        )
        formats.append(fmt)

    # ── DOOH formats (4) ─────────────────────────────────────────────
    _dooh_sized = [
        ("dooh_billboard_1920x1080", "Digital Billboard - 1920x1080", 1920, 1080),
        ("dooh_transit_screen", "Transit Screen", 1920, 1080),
    ]
    for fmt_id, name, w, h in _dooh_sized:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.dooh,
            assets=[make_asset("image"), make_asset("url", "impression_tracker")],
            renders=[make_fixed_renders(width=w, height=h)],
        )
        formats.append(fmt)

    _dooh_unsized = [
        ("dooh_billboard_landscape", "Digital Billboard - Landscape"),
        ("dooh_billboard_portrait", "Digital Billboard - Portrait"),
    ]
    for fmt_id, name in _dooh_unsized:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.dooh,
            assets=[make_asset("image"), make_asset("url", "impression_tracker")],
            renders=None,
        )
        formats.append(fmt)

    # ── Audio formats (3) ────────────────────────────────────────────
    _audio_defs = [
        ("audio_standard_15s", "Standard Audio - 15 seconds"),
        ("audio_standard_30s", "Standard Audio - 30 seconds"),
        ("audio_standard_60s", "Standard Audio - 60 seconds"),
    ]
    for fmt_id, name in _audio_defs:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.audio,
            assets=[make_asset("audio"), make_asset("url", "impression_tracker")],
            renders=None,
        )
        formats.append(fmt)

    # ── Native formats (3) ───────────────────────────────────────────
    _native_defs = [
        ("native_content", "Native Content Placement", ["text", "text", "image", "text", "url"]),
        ("native_standard", "IAB Native Standard", ["text", "text", "image", "image", "text", "url"]),
        ("sponsored_recommendation", "Sponsored Recommendation", ["text", "text", "text", "url", "image", "url"]),
    ]
    for fmt_id, name, asset_types in _native_defs:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        assets = [make_asset(at, f"{at}_asset_{i}") for i, at in enumerate(asset_types)]
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.native,
            assets=assets,
            renders=None,
        )
        formats.append(fmt)

    assert len(formats) == 50, f"Expected 50 formats, got {len(formats)}"
    return [f.model_dump(mode="json") for f in formats]


def main() -> None:
    catalog = _build_catalog()
    out_path = Path(__file__).resolve().parent.parent / ".creative-agent-catalog.json"
    out_path.write_text(json.dumps(catalog, indent=2) + "\n")
    print(f"Wrote {len(catalog)} formats to {out_path}")

    # Summary by type
    from collections import Counter

    type_counts = Counter(f["type"] for f in catalog)
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")


if __name__ == "__main__":
    main()
