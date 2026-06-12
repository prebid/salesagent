"""Pydantic model factories for Format and FormatId.

These are factory-boy factories for Pydantic models (not ORM models),
used by BDD Given steps to construct real Format objects directly.

Usage::

    from tests.factories.format import FormatFactory, make_asset

    # Simple format
    fmt = FormatFactory.build(name="banner")

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

from collections.abc import Callable

import factory
from adcp.types import (
    AudioFormatAsset,
    HtmlFormatAsset,
    ImageFormatAsset,
    MarkdownFormatAsset,
    Responsive,
    TextFormatAsset,
    VideoFormatAsset,
)
from adcp.types.generated_poc.core.format import Dimensions, Renders  # TODO: no stable alias in adcp.types

from src.core.schemas import Format, FormatId

AGENT_URL = "https://creative.adcontextprotocol.org"

# ── Asset class mapping ──────────────────────────────────────────────

_ASSET_CLASS_MAP = {
    "image": ImageFormatAsset,
    "video": VideoFormatAsset,
    "audio": AudioFormatAsset,
    "text": TextFormatAsset,
    "markdown": MarkdownFormatAsset,
    "html": HtmlFormatAsset,
}


def make_asset(asset_type: str, asset_id: str | None = None) -> ImageFormatAsset:
    """Create a typed asset object from an asset type string.

    >>> a = make_asset("video")
    >>> a.asset_type
    'video'
    """
    cls = _ASSET_CLASS_MAP.get(asset_type, ImageFormatAsset)
    return cls(asset_id=asset_id or f"{asset_type}_asset", required=True)


def _find_repeatable_group_class():
    """Return the RepeatableAssetGroup class (stable alias)."""
    from adcp.types import RepeatableAssetGroup

    return RepeatableAssetGroup


def _find_inner_asset_class(asset_type: str):
    """Find the inner asset class for a repeatable group by asset_type.

    Inner assets within a repeatable_group use different classes than
    top-level assets. We find them dynamically to survive SDK regeneration.
    """

    _RepeatableGroupCls = _find_repeatable_group_class()
    # Get the union type from the 'assets' field annotation
    import typing

    assets_field = _RepeatableGroupCls.model_fields["assets"]
    union_args = typing.get_args(typing.get_args(assets_field.annotation)[0])
    for cls in union_args:
        if hasattr(cls, "model_fields") and "asset_type" in cls.model_fields:
            if cls.model_fields["asset_type"].default == asset_type:
                return cls
    # Fallback: use first class
    return union_args[0] if union_args else None


def make_asset_group(
    *asset_types: str,
    group_id: str = "asset_group",
    min_count: int = 1,
    max_count: int = 10,
):
    """Create a repeatable asset group containing typed inner assets.

    All class lookups are dynamic to survive SDK regeneration where
    numbered class names (Assets18→Assets94, etc.) shift.
    """
    _RepeatableGroupCls = _find_repeatable_group_class()
    inner_assets = []
    for at in asset_types:
        inner_cls = _find_inner_asset_class(at)
        if inner_cls:
            inner_assets.append(inner_cls(asset_id=f"{at}_asset", required=True))
    return _RepeatableGroupCls(
        item_type="repeatable_group",
        asset_group_id=group_id,
        required=True,
        min_count=min_count,
        max_count=max_count,
        assets=inner_assets,
    )


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


# ── Reference catalog selection ──────────────────────────────────────


def pick_reference_formats(predicate: Callable[[Format], bool], min_count: int = 1) -> list[Format]:
    """Select formats from the captured reference catalog that satisfy ``predicate``.

    This is the single source for "a format with property X" in scenarios: it draws
    from the same checked-in fixture the in-process harness and the e2e server serve,
    so a selected format is guaranteed to exist on the live server too.

    Raises loud (ValueError) if fewer than ``min_count`` reference formats match —
    that is the true signal the scenario needs a format registered in the creative
    agent's own registry, not a synthetic FormatFactory mint. See issue #1418.

    >>> displays = pick_reference_formats(lambda f: f.format_id.id.startswith("display_"))
    >>> all(f.format_id.id.startswith("display_") for f in displays)
    True
    """
    from src.core.format_cache import load_reference_formats

    matches = [fmt for fmt in load_reference_formats() if predicate(fmt)]
    if len(matches) < min_count:
        raise ValueError(
            f"Reference catalog has {len(matches)} format(s) matching the predicate, "
            f"need at least {min_count}. The scenario needs a format the reference agent "
            "does not serve — register it in the creative agent's registry rather than "
            "minting a synthetic one."
        )
    return matches


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
    is_standard = True


# ── Category mapping (compat shim) ──────────────────────────────────
# FormatCategory was removed in adcp 3.12. Format.type no longer exists.
# BDD steps still pass type= as an extra field (Pydantic ignores in dev mode).
# This mapping provides string values so existing step code doesn't crash.

CATEGORY_MAP: dict[str, str | None] = {
    "display": "display",
    "video": "video",
    "audio": "audio",
    "native": "native",
    "dooh": "dooh",
}
