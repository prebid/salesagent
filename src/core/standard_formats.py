"""Local projection of the AdCP reference creative-format catalog.

Resolves to a :class:`Format` object without a network call to the
reference creative agent (https://creative.adcontextprotocol.org).
Tenants that only use standard formats — the common case — never need
to register a custom creative agent for their own deployment, and
dev/CI environments without internet keep working.

The catalog starts from the SDK's bundled canonical-formats v1 reference
catalog (AdCP 3.1 / SDK 6.1 beta 2) and keeps a few salesagent legacy IDs
as aliases. Any format ID NOT in this catalog falls through to the live
registry lookup, so custom-format tenants are unaffected.

Wired via :func:`CreativeAgentRegistry.get_format` — the registry checks
this catalog first when ``agent_url`` matches the reference creative
agent and the ``format_id`` is in :data:`STANDARD_FORMAT_IDS`. See
``src/core/creative_agent_registry.py``.
"""

from __future__ import annotations

from adcp.canonical_formats.fixtures import load_v1_reference_catalog
from adcp.types import FormatId as LibraryFormatId

# Use the salesagent-extended Format (adds internal platform_config / category /
# requirements fields). The GAM adapter reads format_obj.platform_config for
# creative-placeholder configuration; constructing the library Format directly
# would leave those attributes missing. See Critical Pattern #1 in CLAUDE.md.
from src.core.schemas import Format

# Reference creative agent — every salesagent deployment defaults to it
# regardless of tenant config. See CreativeAgentRegistry.DEFAULT_AGENT.
STANDARD_AGENT_URL = "https://creative.adcontextprotocol.org"


def _format_id(fmt_id: str) -> LibraryFormatId:
    return LibraryFormatId(agent_url=STANDARD_AGENT_URL, id=fmt_id)


def _video_format(format_id: str, name: str, width: int, height: int) -> Format:
    """IAB video — single video asset with dimensions."""
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


def _format_from_fixture(raw: dict) -> Format:
    """Parse one SDK reference-catalog entry into the local extended Format."""
    return Format.model_validate(raw)


def _clone_with_legacy_id(source: Format, legacy_id: str, name: str | None = None) -> Format:
    """Clone an SDK reference format under a salesagent legacy ID."""
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
        "audio_30s": ("audio_standard_30s", "Audio 30s"),
        "audio_60s": ("audio_standard_60s", "Audio 60s"),
        "native_1x1": ("native_standard", "Native 1:1"),
    }
    aliases = {
        legacy_id: _clone_with_legacy_id(formats[source_id], legacy_id, name)
        for legacy_id, (source_id, name) in alias_sources.items()
    }
    # The beta 2 reference catalog does not include this old SD 4:3 fixture,
    # but existing products can still reference it.
    aliases["video_640x480"] = _video_format("video_640x480", "Video SD 4:3", 640, 480)
    return aliases


_SDK_REFERENCE_FORMATS = _build_sdk_reference_formats()

# Anything in this dict short-circuits the network round trip.
STANDARD_FORMATS: dict[str, Format] = {
    **_SDK_REFERENCE_FORMATS,
    **_build_legacy_aliases(_SDK_REFERENCE_FORMATS),
}


# Lookup-friendly set for fast membership checks (the registry uses this
# before constructing the Format object).
STANDARD_FORMAT_IDS: frozenset[str] = frozenset(STANDARD_FORMATS.keys())


def get_standard_format(format_id: str) -> Format | None:
    """Return the hardcoded :class:`Format` for ``format_id``, or None.

    Caller compares ``agent_url`` to :data:`STANDARD_AGENT_URL` first —
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
    # Pydantic AnyUrl is not a str — coerce so .rstrip works regardless.
    return str(agent_url).rstrip("/") == STANDARD_AGENT_URL.rstrip("/")


__all__: list[str] = [
    "STANDARD_AGENT_URL",
    "STANDARD_FORMAT_IDS",
    "STANDARD_FORMATS",
    "get_standard_format",
    "is_standard_agent",
]
