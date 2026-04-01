"""Pydantic model factories for Format and FormatId.

These are factory-boy factories for Pydantic models (not ORM models),
used by BDD Given steps to construct real Format objects directly.

Usage::

    from tests.factories.format import FormatFactory, make_asset

    # Simple format
    fmt = FormatFactory.build(name="banner", type=FormatCategory.display)

    # With assets
    fmt = FormatFactory.build(
        name="rich-ad",
        assets=[make_asset("image"), make_asset("video")],
    )

    # With render dimensions
    fmt = FormatFactory.build(
        name="leaderboard",
        renders=[make_renders(width=728, height=90)],
    )
"""

from __future__ import annotations

import factory
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
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import Format, FormatId

AGENT_URL = "https://creative.adcontextprotocol.org"

# ── Asset class mapping ──────────────────────────────────────────────

_ASSET_CLASS_MAP = {
    "image": Assets,
    "video": Assets5,
    "audio": Assets6,
    "text": Assets7,
    "markdown": Assets8,
    "html": Assets9,
}


def make_asset(asset_type: str, asset_id: str | None = None) -> Assets:
    """Create a typed asset object from an asset type string.

    >>> a = make_asset("video")
    >>> a.asset_type
    'video'
    """
    cls = _ASSET_CLASS_MAP.get(asset_type, Assets)
    return cls(asset_id=asset_id or f"{asset_type}_asset", required=True)


def make_renders(
    *,
    width: int | None = None,
    height: int | None = None,
    min_width: int | None = None,
    max_width: int | None = None,
    responsive_width: bool | None = None,
) -> Renders:
    """Create a Renders object with dimensions.

    >>> r = make_renders(width=728, height=90)
    >>> r.dimensions.width
    728
    """
    dims_kwargs: dict = {}
    if width is not None:
        dims_kwargs["width"] = width
    if height is not None:
        dims_kwargs["height"] = height
    if min_width is not None:
        dims_kwargs["min_width"] = min_width
    if max_width is not None:
        dims_kwargs["max_width"] = max_width
    if responsive_width is not None:
        dims_kwargs["responsive"] = Responsive(width=responsive_width, height=False)
    return Renders(role="primary", dimensions=Dimensions(**dims_kwargs))


def make_responsive_renders() -> Renders:
    """Create a responsive Renders object with standard defaults."""
    return make_renders(min_width=300, max_width=970, responsive_width=True)


def make_fixed_renders(width: int = 728, height: int = 90) -> Renders:
    """Create a fixed-dimension Renders object."""
    return make_renders(width=width, height=height)


# ── Factories ────────────────────────────────────────────────────────


class FormatIdFactory(factory.Factory):
    """Factory for FormatId Pydantic model."""

    class Meta:
        model = FormatId

    agent_url = AGENT_URL
    id = factory.Sequence(lambda n: f"fmt_{n}")


class FormatFactory(factory.Factory):
    """Factory for Format Pydantic model.

    Only ``format_id`` and ``name`` are required. All other fields
    are optional and can be passed as keyword overrides.
    """

    class Meta:
        model = Format

    format_id = factory.SubFactory(FormatIdFactory)
    name = factory.Sequence(lambda n: f"format_{n}")
    type = FormatCategory.display
    is_standard = True


# ── Category mapping ─────────────────────────────────────────────────

CATEGORY_MAP: dict[str, FormatCategory] = {
    "display": FormatCategory.display,
    "video": FormatCategory.video,
    "audio": FormatCategory.audio,
    "native": FormatCategory.native,
    "dooh": FormatCategory.dooh,
}
