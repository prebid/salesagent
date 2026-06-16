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

    The ``assets`` field annotation is nested:
        list[Annotated[Union[Annotated[AssetX, Tag], ...], Discriminator]]
    so we unwrap the list element (Annotated), then the discriminated Union,
    then each member (Annotated) down to the concrete asset model.
    """
    from typing import Annotated, get_args, get_origin

    def _unwrap_annotated(tp):
        return get_args(tp)[0] if get_origin(tp) is Annotated else tp

    _RepeatableGroupCls = _find_repeatable_group_class()
    assets_field = _RepeatableGroupCls.model_fields["assets"]
    # list[...] -> element type (Annotated[Union[...], Discriminator])
    element = _unwrap_annotated(get_args(assets_field.annotation)[0])
    # Union[Annotated[AssetX, Tag], ...] -> concrete member classes
    members = [_unwrap_annotated(m) for m in get_args(element)]
    members = [m for m in members if hasattr(m, "model_fields")]
    for cls in members:
        field = cls.model_fields.get("asset_type")
        if field is not None and field.default == asset_type:
            return cls
    # Fallback: first concrete member
    return members[0] if members else None


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
