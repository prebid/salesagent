"""Local projection of the AdCP reference creative-format catalog.

Resolves to a :class:`Format` object without a network call to the
reference creative agent (https://creative.adcontextprotocol.org).
Tenants that only use standard formats - the common case - never need
to register a custom creative agent for their own deployment, and
dev/CI environments without internet keep working.

The catalog starts from the SDK's bundled canonical-formats v1 reference
catalog (AdCP 3.1 beta.4) and keeps salesagent legacy IDs plus local
canonical aliases used by adapters. Any format ID NOT in this catalog
falls through to the live registry lookup, so custom-format tenants are
unaffected.

Wired via :func:`CreativeAgentRegistry.get_format` - the registry checks
this catalog first when ``agent_url`` matches the reference creative
agent and the ``format_id`` is in :data:`STANDARD_FORMAT_IDS`. See
``src/core/creative_agent_registry.py``.
"""

from __future__ import annotations

from typing import Any

from adcp.canonical_formats.fixtures import load_v1_reference_catalog
from adcp.types import FormatId as LibraryFormatId

from src.core.canonical_formats import CANONICAL_FORMAT_IDS, DEFAULT_CREATIVE_AGENT_URL

# Use the salesagent-extended Format (adds internal platform_config / category /
# requirements fields). The GAM adapter reads format_obj.platform_config for
# creative-placeholder configuration; constructing the library Format directly
# would leave those attributes missing. See Critical Pattern #1 in CLAUDE.md.
from src.core.schemas import Format

# Reference creative agent - every salesagent deployment defaults to it
# regardless of tenant config. See CreativeAgentRegistry.DEFAULT_AGENT.
STANDARD_AGENT_URL = DEFAULT_CREATIVE_AGENT_URL


def _format_id(fmt_id: str) -> LibraryFormatId:
    return LibraryFormatId(agent_url=STANDARD_AGENT_URL, id=fmt_id)


def _legacy_video_format(format_id: str, name: str, width: int, height: int) -> Format:
    """Legacy IAB video alias - single video asset with dimensions."""
    return Format(
        format_id=_format_id(format_id),
        name=name,
        type="video",
        assets=[
            {
                "item_type": "individual",
                "asset_id": "video",
                "asset_type": "video",
                "required": True,
                "width": width,
                "height": height,
            },
        ],
    )


def _carousel_format(format_id: str, name: str, group_id: str, min_count: int, max_count: int) -> Format:
    """Canonical carousel/multi-asset display format."""
    return Format(
        format_id=_format_id(format_id),
        name=name,
        type="display",
        assets=[
            {
                "item_type": "repeatable_group",
                "asset_group_id": group_id,
                "required": True,
                "min_count": min_count,
                "max_count": max_count,
                "assets": [
                    {"asset_id": "image", "asset_type": "image", "required": True},
                    {"asset_id": "title", "asset_type": "text", "required": False},
                    {"asset_id": "caption", "asset_type": "text", "required": False},
                    {"asset_id": "click_url", "asset_type": "url", "required": False},
                ],
            },
            {"item_type": "individual", "asset_id": "brand_logo", "asset_type": "image", "required": False},
        ],
    )


def _mobile_story_format() -> Format:
    """Canonical vertical mobile story sequence."""
    return Format(
        format_id=_format_id("mobile_story_vertical"),
        name="Mobile Story Vertical",
        type="display",
        assets=[
            {
                "item_type": "repeatable_group",
                "asset_group_id": "frame",
                "required": True,
                "min_count": 3,
                "max_count": 7,
                "assets": [
                    {"asset_id": "background", "asset_type": "image", "required": True},
                    {"asset_id": "headline", "asset_type": "text", "required": True},
                    {"asset_id": "body", "asset_type": "text", "required": False},
                ],
            },
            {"item_type": "individual", "asset_id": "brand_logo", "asset_type": "image", "required": False},
        ],
    )


def _video_playlist_format() -> Format:
    """Canonical video playlist / bumper sequence."""
    return Format(
        format_id=_format_id("video_playlist_6s_bumpers"),
        name="Video Playlist 6s Bumpers",
        type="video",
        assets=[
            {
                "item_type": "repeatable_group",
                "asset_group_id": "clip",
                "required": True,
                "min_count": 2,
                "max_count": 5,
                "assets": [{"asset_id": "video", "asset_type": "video", "required": True, "duration_ms": 6000}],
            }
        ],
    )


def _audio_vast_format() -> Format:
    """Canonical VAST audio tag format."""
    return Format.model_validate(
        {
            "format_id": {"agent_url": STANDARD_AGENT_URL, "id": "audio_vast"},
            "name": "VAST Audio",
            "type": "audio",
            "description": "Audio ad via VAST tag (supports any duration)",
            "accepts_parameters": ["duration"],
            "assets": [
                {
                    "item_type": "individual",
                    "asset_id": "vast_tag",
                    "asset_type": "vast",
                    "required": True,
                }
            ],
            "canonical": {"kind": "audio_daast"},
        }
    )


def _normalize_tracker_asset(asset: dict[str, Any]) -> dict[str, Any]:
    """Convert SDK beta tracker assets to the current schema's URL shape."""
    if asset.get("item_type") != "individual" or asset.get("asset_type") != "pixel_tracker":
        return asset

    asset_id = str(asset.get("asset_id") or "")
    role_by_id = {
        "impression_tracker": "impression_tracker",
        "viewability_tracker": "viewability_tracker",
        "click_tracker": "click_tracker",
    }
    role = role_by_id.get(asset_id, "third_party_tracker")
    raw_requirements = asset.get("requirements")
    requirements: dict[str, Any] = raw_requirements if isinstance(raw_requirements, dict) else {}

    return {
        "item_type": "individual",
        "asset_id": asset_id,
        "asset_role": asset.get("asset_role") or role,
        "asset_type": "url",
        "required": bool(asset.get("required", False)),
        "requirements": {
            "role": role,
            "protocols": ["https"],
            "macro_support": True,
            **requirements,
        },
    }


def _normalize_sdk_assets(value: Any) -> Any:
    """Recursively normalize SDK fixture assets before publishing locally."""
    if isinstance(value, list):
        return [_normalize_sdk_assets(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized = _normalize_tracker_asset(dict(value))
    if "assets" in normalized:
        normalized["assets"] = _normalize_sdk_assets(normalized["assets"])
    return normalized


def _format_from_fixture(raw: dict) -> Format:
    """Parse one SDK reference-catalog entry into the local extended Format."""
    payload = dict(raw)
    if "assets" in payload:
        payload["assets"] = _normalize_sdk_assets(payload["assets"])
    if "assets_required" in payload:
        payload["assets_required"] = _normalize_sdk_assets(payload["assets_required"])
    return Format.model_validate(payload)


def _clone_with_legacy_id(source: Format, legacy_id: str, name: str | None = None) -> Format:
    """Clone an SDK reference format under a salesagent legacy or adapter-local ID."""
    payload = source.model_dump(mode="json", exclude_none=True)
    payload["format_id"] = {"agent_url": STANDARD_AGENT_URL, "id": legacy_id}
    if name is not None:
        payload["name"] = name
    return Format.model_validate(payload)


def _build_sdk_reference_formats() -> dict[str, Format]:
    formats: dict[str, Format] = {}
    for raw in load_v1_reference_catalog():
        fmt = _format_from_fixture(raw)
        formats[fmt.format_id.id] = fmt
    return formats


def _build_legacy_aliases(formats: dict[str, Format]) -> dict[str, Format]:
    """Legacy IDs still used by seed data/tests, backed by SDK entries where possible."""
    alias_sources = {
        "display_300x250": ("display_300x250_image", "Medium Rectangle"),
        "display_728x90": ("display_728x90_image", "Leaderboard"),
        "display_160x600": ("display_160x600_image", "Wide Skyscraper"),
        "display_300x600": ("display_300x600_image", "Half Page"),
        "display_320x50": ("display_320x50_image", "Mobile Banner"),
        "display_970x250": ("display_970x250_image", "Billboard"),
        "audio_15s": ("audio_standard_15s", "Audio 15s"),
        "audio_30s": ("audio_standard_30s", "Audio 30s"),
        "audio_60s": ("audio_standard_60s", "Audio 60s"),
        "native_1x1": ("native_standard", "Native 1:1"),
    }
    aliases = {
        legacy_id: _clone_with_legacy_id(formats[source_id], legacy_id, name)
        for legacy_id, (source_id, name) in alias_sources.items()
        if source_id in formats
    }
    # The beta.4 reference catalog does not include this old SD 4:3 fixture,
    # but existing products can still reference it.
    aliases["video_640x480"] = _legacy_video_format("video_640x480", "Video SD 4:3", 640, 480)
    return aliases


def _build_local_canonical_extensions() -> dict[str, Format]:
    """Canonical adapter formats not yet present in the SDK fixture catalog."""
    return {
        "product_carousel_display": _carousel_format(
            "product_carousel_display", "Product Carousel Display", "product", 2, 5
        ),
        "image_slideshow_5s_each": _carousel_format(
            "image_slideshow_5s_each", "Image Slideshow 5s Each", "slide", 3, 8
        ),
        "mobile_story_vertical": _mobile_story_format(),
        "video_playlist_6s_bumpers": _video_playlist_format(),
        "audio_vast": _audio_vast_format(),
    }


_SDK_REFERENCE_FORMATS = _build_sdk_reference_formats()

# Anything in this dict short-circuits the network round trip.
STANDARD_FORMATS: dict[str, Format] = {
    **_SDK_REFERENCE_FORMATS,
    **_build_legacy_aliases(_SDK_REFERENCE_FORMATS),
    **_build_local_canonical_extensions(),
}

_missing_canonical_formats = CANONICAL_FORMAT_IDS - STANDARD_FORMATS.keys()
assert not _missing_canonical_formats, f"Missing canonical formats: {sorted(_missing_canonical_formats)}"


# Lookup-friendly set for fast membership checks (the registry uses this
# before constructing the Format object).
STANDARD_FORMAT_IDS: frozenset[str] = frozenset(STANDARD_FORMATS.keys())


def get_standard_format(format_id: str) -> Format | None:
    """Return the hardcoded :class:`Format` for ``format_id``, or None.

    Caller compares ``agent_url`` to :data:`STANDARD_AGENT_URL` first -
    only standard-agent format requests should hit this catalog.
    """
    return STANDARD_FORMATS.get(format_id)


def is_standard_agent(agent_url: str) -> bool:
    """``True`` if ``agent_url`` matches the reference creative agent.

    Tolerant of trailing slashes and protocol normalization (the YARL
    canonicalization the registry uses on cache keys is overkill for this
    boolean check).
    """
    if not agent_url:
        return False
    # Pydantic AnyUrl is not a str - coerce so .rstrip works regardless.
    return str(agent_url).rstrip("/") == STANDARD_AGENT_URL.rstrip("/")


__all__: list[str] = [
    "STANDARD_AGENT_URL",
    "STANDARD_FORMAT_IDS",
    "STANDARD_FORMATS",
    "get_standard_format",
    "is_standard_agent",
]
