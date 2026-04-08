#!/usr/bin/env python3
"""Generate .creative-agent-catalog.json with a realistic format catalog.

This script produces a 49-format catalog that mirrors what a real creative
agent would serve. The catalog is used by BDD tests (via load_real_catalog())
to replace the previous 1-2 format mock data with realistic format definitions.

Distribution: 28 display, 12 video, 4 dooh, 3 audio, 2 native = 49 total.

Usage:
    uv run python scripts/refresh-creative-catalog.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adcp.types.generated_poc.enums.disclosure_position import DisclosurePosition
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
    """Build the 49-format catalog."""
    formats = []

    # ── Display formats (28) ─────────────────────────────────────────
    display_defs = [
        # Standard IAB sizes
        ("display_300x250_image", "Medium Rectangle (300x250)", 300, 250, ["image"]),
        ("display_728x90_image", "Leaderboard (728x90)", 728, 90, ["image"]),
        ("display_160x600_image", "Wide Skyscraper (160x600)", 160, 600, ["image"]),
        ("display_320x50_image", "Mobile Banner (320x50)", 320, 50, ["image"]),
        ("display_970x250_image", "Billboard (970x250)", 970, 250, ["image"]),
        ("display_300x600_image", "Half Page (300x600)", 300, 600, ["image"]),
        ("display_336x280_image", "Large Rectangle (336x280)", 336, 280, ["image"]),
        ("display_970x90_image", "Super Leaderboard (970x90)", 970, 90, ["image"]),
        ("display_468x60_image", "Full Banner (468x60)", 468, 60, ["image"]),
        ("display_120x600_image", "Skyscraper (120x600)", 120, 600, ["image"]),
        ("display_320x100_image", "Large Mobile Banner (320x100)", 320, 100, ["image"]),
        ("display_250x250_image", "Square (250x250)", 250, 250, ["image"]),
        # HTML5 / rich media variants
        ("display_300x250_html5", "HTML5 Medium Rectangle", 300, 250, ["html"]),
        ("display_728x90_html5", "HTML5 Leaderboard", 728, 90, ["html"]),
        ("display_320x50_html5", "HTML5 Mobile Banner", 320, 50, ["html"]),
        ("display_970x250_html5", "HTML5 Billboard", 970, 250, ["html"]),
        # Multi-asset formats
        ("display_carousel", "Display Carousel", 300, 250, ["image", "text"]),
        ("display_expandable", "Expandable Banner", 300, 250, ["image", "html"]),
        # Responsive formats
        ("display_responsive_flex", "Responsive Flex Banner", None, None, ["image"]),
        ("display_responsive_smart", "Responsive Smart Banner", None, None, ["image", "text"]),
        # Generative
        ("display_generative", "Generative Display Ad", 300, 250, ["image", "text"]),
        ("display_dynamic_creative", "Dynamic Creative Optimization", 300, 250, ["image", "text"]),
        # Interstitial
        ("display_interstitial_320x480", "Mobile Interstitial (320x480)", 320, 480, ["image"]),
        ("display_interstitial_768x1024", "Tablet Interstitial (768x1024)", 768, 1024, ["image"]),
        # Adhesion / sticky
        ("display_adhesion_320x50", "Mobile Adhesion (320x50)", 320, 50, ["image"]),
        ("display_adhesion_728x90", "Desktop Adhesion (728x90)", 728, 90, ["image"]),
        # Skin / wallpaper
        ("display_skin", "Page Skin", 1800, 1000, ["image", "html"]),
        # Lightbox
        ("display_lightbox", "Lightbox Overlay", 600, 400, ["image", "html"]),
    ]

    for fmt_id, name, width, height, asset_types in display_defs:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        assets = [make_asset(at) for at in asset_types]
        if width is None:
            renders = [make_responsive_renders()]
            is_responsive = True
        else:
            renders = [make_fixed_renders(width=width, height=height)]
            is_responsive = False
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.display,
            assets=assets,
            renders=renders,
        )
        formats.append(fmt)

    # ── Video formats (12) ───────────────────────────────────────────
    video_defs = [
        ("video_standard", "Standard Video (16:9)", 1920, 1080, ["video"]),
        ("video_vertical", "Vertical Video (9:16)", 1080, 1920, ["video"]),
        ("video_square", "Square Video (1:1)", 1080, 1080, ["video"]),
        ("video_preroll_15s", "Pre-Roll 15s", 1920, 1080, ["video"]),
        ("video_preroll_30s", "Pre-Roll 30s", 1920, 1080, ["video"]),
        ("video_midroll", "Mid-Roll", 1920, 1080, ["video"]),
        ("video_outstream", "Outstream Video", 640, 360, ["video"]),
        ("video_bumper_6s", "Bumper Ad 6s", 1920, 1080, ["video"]),
        ("video_rewarded", "Rewarded Video", 1920, 1080, ["video"]),
        ("video_interactive", "Interactive Video", 1920, 1080, ["video", "html"]),
        ("video_shoppable", "Shoppable Video", 1920, 1080, ["video", "image"]),
        ("video_ctv_standard", "Connected TV Standard", 1920, 1080, ["video"]),
    ]

    for fmt_id, name, width, height, asset_types in video_defs:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        assets = [make_asset(at) for at in asset_types]
        renders = [make_fixed_renders(width=width, height=height)]
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.video,
            assets=assets,
            renders=renders,
            supported_disclosure_positions=[
                DisclosurePosition.prominent,
                DisclosurePosition.overlay,
            ],
        )
        formats.append(fmt)

    # ── DOOH formats (4) ─────────────────────────────────────────────
    dooh_defs = [
        ("dooh_billboard_1920x1080", "Digital Billboard (1920x1080)", 1920, 1080, ["image"]),
        ("dooh_portrait_1080x1920", "Digital Portrait (1080x1920)", 1080, 1920, ["image"]),
        ("dooh_transit_1280x720", "Transit Screen (1280x720)", 1280, 720, ["image"]),
        ("dooh_video_billboard", "Video Billboard", 1920, 1080, ["video"]),
    ]

    for fmt_id, name, width, height, asset_types in dooh_defs:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        assets = [make_asset(at) for at in asset_types]
        renders = [make_fixed_renders(width=width, height=height)]
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.dooh,
            assets=assets,
            renders=renders,
        )
        formats.append(fmt)

    # ── Audio formats (3) ────────────────────────────────────────────
    audio_defs = [
        ("audio_standard_15s", "Audio Ad 15s", ["audio"]),
        ("audio_standard_30s", "Audio Ad 30s", ["audio"]),
        ("audio_companion", "Audio with Companion Banner", ["audio", "image"]),
    ]

    for fmt_id, name, asset_types in audio_defs:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        assets = [make_asset(at) for at in asset_types]
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.audio,
            assets=assets,
            renders=None,
            supported_disclosure_positions=[DisclosurePosition.audio],
        )
        formats.append(fmt)

    # ── Native formats (2) ───────────────────────────────────────────
    native_defs = [
        ("native_standard", "Native Standard", ["image", "text"]),
        ("native_video", "Native Video", ["video", "text"]),
    ]

    for fmt_id, name, asset_types in native_defs:
        fid = FormatIdFactory.build(agent_url=AGENT_URL, id=fmt_id)
        assets = [make_asset(at) for at in asset_types]
        fmt = FormatFactory.build(
            format_id=fid,
            name=name,
            type=FormatCategory.native,
            assets=assets,
            renders=[make_responsive_renders()],
        )
        formats.append(fmt)

    assert len(formats) == 49, f"Expected 49 formats, got {len(formats)}"
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
