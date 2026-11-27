"""The one reader of a stored creative for trafficking: ``Creative.data`` in, the carrier out.

The carrier, its representations and its sizes live in the leaf ``src/core/traffic_carrier.py``,
which the adapters import too. Each asset the reader serves from is parsed through the pinned
asset union (``adcp.types.AssetVariant``) first, so an asset missing a member its schema
requires, such as a zip with no ``url`` (``core/assets/zip-asset.json`` requires
``["asset_type", "url"]``), carries nothing an adapter can serve.

Which one a creative carries is read off the asset's own ``asset_type`` (and, for a URL, the
url-asset ladder), never off the kind alone: the reference creative agent declares its
``display_*_html`` formats as kind ``html5`` while their one asset is inline ``html``.

Sizes come from the product's format option, else the legacy format's primary render, and a
media file's own dimensions win over both. ``formats/canonical/display_tag.json`` gives an option
exactly one size mode: "Exactly one of ``(width, height)``, ``sizes[]``, or ``min/max_width`` +
``min/max_height``". A fixed or multi-size option lands in ``sizes``; a responsive one in
``size_ranges`` ("Minimum accepted width for responsive third-party tags. Pair with
``max_width``"). A creative carrying neither declared no size, and the adapter may traffic it at
any size its inventory takes.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from adcp.types import AssetVariant as LibraryAssetVariant
from adcp.types.domains.core.assets.asset_union import DisplayTagAsset as LibraryDisplayTagAsset
from adcp.types.domains.core.assets.asset_union import HtmlAsset as LibraryHtmlAsset
from adcp.types.domains.core.assets.asset_union import UrlAsset as LibraryUrlAsset
from adcp.types.domains.core.assets.asset_union import ZipAsset as LibraryZipAsset
from pydantic import TypeAdapter, ValidationError

from src.core.format_satisfaction import (
    RANGE_BOUNDS,
    declaration_slot_asset_ids,
    declared_size_mode,
    declared_slots_by_kind,
    size_mode,
)
from src.core.helpers.creative_helpers import (
    _read_media_assets,
    extract_click_url,
    extract_impression_tracker_url,
    url_asset_identification,
)
from src.core.traffic_carrier import (
    CreativeToTraffic,
    Html5BundleRepresentation,
    HtmlRepresentation,
    InlineMarkupRepresentation,
    MediaRepresentation,
    MediaType,
    PairedRedirectRepresentation,
    Representation,
    TagUrlRepresentation,
    TrafficSize,
    TrafficSizeRange,
)

#: The media types, in the order an option's declared slots are asked for one.
_MEDIA_TYPES: dict[str, MediaType] = {"image": "image", "video": "video", "audio": "audio"}

#: The pinned union of every asset type, discriminated by ``asset_type`` (a display tag by its
#: ``delivery_type`` too). An asset is served only as the typed value this parses it to.
_ASSET: TypeAdapter[LibraryAssetVariant] = TypeAdapter(LibraryAssetVariant)


class TrafficRefusalReason(StrEnum):
    """Why a stored creative cannot be handed to an adapter."""

    NO_SERVING_CONTENT = "no_serving_content"
    """No asset the creative carries is something an ad server can serve."""

    NO_DIMENSIONS = "no_dimensions"
    """Its media is an image or video file, and nothing states the file's size."""


@dataclass(frozen=True)
class TrafficRefusal:
    """The reader's answer for a creative it cannot carry: which one, and why."""

    creative_id: str
    reason: TrafficRefusalReason


def read_creative_to_traffic(
    creative_id: str,
    creative_data: Mapping[str, Any],
    kind: str,
    *,
    format_spec: Any | None,
    declarations: Sequence[Any],
) -> CreativeToTraffic | TrafficRefusal:
    """The ONE reader of a stored creative for trafficking. Pure: it fetches and writes nothing.

    Args:
        creative_id: the creative's id.
        creative_data: the stored ``Creative.data`` document.
        kind: the creative's canonical ``format_kind`` (``CreativeFormatIdentity.kind``).
        format_spec: the legacy format declaration its retained reference resolved to, or
            ``None`` when it retains none.
        declarations: the product format declarations the creative RESOLVED to on the path
            pushing it. Required, with no default: an empty tuple reads as "the option states
            no size", which lets the adapter pick any size, so a caller that has not resolved
            the product must refuse rather than pass nothing.
    """
    data = dict(creative_data)
    assets = _assets(data)
    option_sizes, option_size_ranges = _declared_sizes(declarations, format_spec)
    sizes, size_ranges = option_sizes, option_size_ranges
    try:
        representation = _representation(data, assets, kind, format_spec, declarations)
    except ValidationError:
        # A stored asset that does not fit its pinned schema, a member missing or a
        # markup_type outside the pinned enum, carries nothing an adapter can serve.
        representation = None
    if representation is None:
        return TrafficRefusal(creative_id, TrafficRefusalReason.NO_SERVING_CONTENT)

    if isinstance(representation, MediaRepresentation):
        _url, width, height, owes_dimensions, _type = _read_media_assets(data, format_spec, kind, declarations)
        if width and height:
            sizes, size_ranges = (TrafficSize(width=width, height=height),), ()
        elif not sizes and owes_dimensions:
            return TrafficRefusal(creative_id, TrafficRefusalReason.NO_DIMENSIONS)

    impression_tracker = extract_impression_tracker_url(data, format_spec)
    return CreativeToTraffic(
        creative_id=creative_id,
        format_kind=kind,
        representation=representation,
        sizes=sizes,
        size_ranges=size_ranges,
        option_sizes=option_sizes,
        option_size_ranges=option_size_ranges,
        clickthrough_url=(
            None
            if isinstance(representation, PairedRedirectRepresentation)
            else extract_click_url(data, format_spec, apply_macro_substitution=False)
        ),
        impression_tracker_urls=(impression_tracker,) if impression_tracker else (),
    )


def _representation(
    data: dict[str, Any],
    assets: Mapping[str, Mapping[str, Any]],
    kind: str,
    format_spec: Any | None,
    declarations: Sequence[Any],
) -> Representation | None:
    """The representation the buyer sent, most specific first; ``None`` when there is none."""
    if format_spec is not None and getattr(format_spec, "output_format_ids", None):
        return _generated(data, kind, declarations)

    if isinstance(tag := _parsed_first(assets, "display_tag"), LibraryDisplayTagAsset):
        return _display_tag(tag)
    if isinstance(bundle := _parsed_first(assets, "zip"), LibraryZipAsset):
        return Html5BundleRepresentation(url=str(bundle.url), backup_image_url=_backup_image_url(assets, bundle))
    if isinstance(html := _parsed_first(assets, "html"), LibraryHtmlAsset):
        return HtmlRepresentation(content=html.content) if html.content else None
    if ad_request_url := _ad_request_url(data, assets, format_spec):
        return TagUrlRepresentation(url=ad_request_url)
    if kind in _NON_MEDIA_KINDS:
        # The kind is served as markup, a bundle or a tag, and the creative carries none of
        # them. Its root ``url`` is not a substitute: for these kinds sync wrote the creative
        # agent's preview page there.
        return None

    url, _width, _height, _owes_dimensions, url_asset_type = _read_media_assets(data, format_spec, kind, declarations)
    if not url:
        return None
    return MediaRepresentation(asset_type=_media_asset_type(url_asset_type, kind, declarations), url=str(url))


#: Kinds whose serving content is never a plain media file.
_NON_MEDIA_KINDS = frozenset({"display_tag", "html5"})


def _display_tag(tag: LibraryDisplayTagAsset) -> Representation:
    """One parsed ``display_tag`` asset as its delivery type's arm."""
    asset = tag.root
    if asset.delivery_type == "tag_url":
        return TagUrlRepresentation(url=str(asset.url))
    if asset.delivery_type == "inline_markup":
        return InlineMarkupRepresentation(markup=asset.markup, markup_type=asset.markup_type)
    return PairedRedirectRepresentation(
        ad_request_url=str(asset.ad_request_url), clickthrough_url=str(asset.clickthrough_url)
    )


def _ad_request_url(
    data: dict[str, Any], assets: Mapping[str, Mapping[str, Any]], format_spec: Any | None
) -> str | None:
    """A legacy ``tag_url``: a url asset the url-asset ladder identifies as an ``ad_request``.

    Nothing else reads as one, not even a url asset in the display_tag kind's ``tag_url`` slot
    that states no ``url_type``. AdCP 3.2.7 ``core/assets/url-asset.json``: "third_party_tracker
    and display ad requests have no safe fallback. When neither ``url_type`` nor a format-side
    role is available, receivers MUST NOT silently pick a mechanism." ``docs/creative/
    asset-types.mdx``: "Display ad-request URLs also have no role-based fallback. Declare
    ``url_type: "ad_request"`` explicitly." The slot's "MUST use ``url_type: ad_request``"
    (``formats/canonical/display_tag.json``) obliges the sender, not the receiver.
    """
    mechanisms, _unidentified = url_asset_identification(data, format_spec)
    for asset_id, asset in assets.items():
        if asset.get("asset_type") != "url" or mechanisms.get(asset_id) != "ad_request":
            continue
        if isinstance(url_asset := _ASSET.validate_python(asset), LibraryUrlAsset):
            return str(url_asset.url)
    return None


def _generated(data: dict[str, Any], kind: str, declarations: Sequence[Any]) -> Representation | None:
    """A generative creative's build output, read as a synced manifest would be.

    What ``build_creative`` produced is what is served, never a preview. Build output that
    carries assets goes through the same ladder as a buyer's manifest. Output that is only a
    URL (``output_format.url``, as sync stores it) names no asset type, so it is read as a file
    only for a kind whose serving content IS a file; for any other kind a bare URL says nothing
    about how to serve it. A creative with no build output has nothing to serve yet.
    """
    output = data.get("output_format")
    if not isinstance(output, Mapping):
        return None
    output_assets = _assets(output)
    if output_assets:
        return _representation(
            {**data, "assets": dict(output_assets), "url": None}, output_assets, kind, None, declarations
        )
    url = output.get("url")
    media_type = _required_media_type(kind)
    if not url or media_type is None:
        return None
    return MediaRepresentation(asset_type=media_type, url=str(url))


def _required_media_type(kind: str) -> MediaType | None:
    """The media type of the file the kind requires: ``image``, ``video_hosted``, ``audio_hosted``.

    Read off the kind's declared slots, so a kind whose media is optional (a native card's
    ``main_image``, a tag's ``backup_image``, a video's ``thumbnail``) is not answered by it.
    """
    for slot in declared_slots_by_kind().get(kind, ()):
        media_type = _MEDIA_TYPES.get(str(slot.get("asset_type")))
        if media_type and slot.get("required"):
            return media_type
    return None


def _media_asset_type(url_asset_type: str | None, kind: str, declarations: Sequence[Any]) -> MediaType:
    """The media type the creative's file is: the type of the asset its URL came from, else the
    kind's required media, else an option's.

    The asset that supplied the URL, not the first media asset in the manifest: an image
    option may also take a video (formats/canonical/image.json ``slots``: "Products MAY
    override the default"), and the served file is still the image.
    """
    if own := _MEDIA_TYPES.get(str(url_asset_type)):
        return own
    if required := _required_media_type(kind):
        return required
    for media_type in _MEDIA_TYPES.values():
        if any(declaration_slot_asset_ids(declaration, {media_type}) for declaration in declarations):
            return media_type
    return "image"


def _backup_image_url(assets: Mapping[str, Mapping[str, Any]], bundle: LibraryZipAsset) -> str | None:
    """The html5 kind's ``backup_image`` slot, else the zip asset's own ``backup_image_url``.

    The slot is not parsed as an image asset: ``core/assets/image-asset.json`` requires a
    width and height, which a backup image the adapter sizes from the option need not state.
    """
    backup = assets.get("backup_image") or {}
    if backup.get("asset_type") == "image" and (url := backup.get("url")):
        return str(url)
    return str(bundle.backup_image_url) if bundle.backup_image_url else None


def _parsed_first(assets: Mapping[str, Mapping[str, Any]], asset_type: str) -> LibraryAssetVariant | None:
    """The first asset of *asset_type*, in asset-id order so two reads of one creative agree, parsed.

    Raises:
        ValidationError: the asset does not fit its pinned schema, for example a zip with no
            ``url`` or a display tag with no ``delivery_type``.
    """
    for asset_id in sorted(assets):
        if assets[asset_id].get("asset_type") == asset_type:
            return _ASSET.validate_python(assets[asset_id])
    return None


def _assets(data: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The manifest's single-valued assets. A repeatable slot stores a list, which no representation reads."""
    raw = data.get("assets")
    if not isinstance(raw, Mapping):
        return {}
    return {str(asset_id): asset for asset_id, asset in raw.items() if isinstance(asset, Mapping)}


def _declared_sizes(
    declarations: Sequence[Any], format_spec: Any | None
) -> tuple[tuple[TrafficSize, ...], tuple[TrafficSizeRange, ...]]:
    """The sizes and responsive ranges the format accepts, over every declaration, else the legacy render."""
    modes = [declared_size_mode(declaration) for declaration in declarations]
    if not any(sizes or ranges for sizes, ranges in modes) and format_spec is not None:
        for render in getattr(format_spec, "renders", None) or ():
            dimensions = getattr(render, "dimensions", None)
            if getattr(render, "role", None) == "primary" and dimensions is not None:
                modes = [
                    size_mode({name: getattr(dimensions, name, None) for name in ("width", "height", *RANGE_BOUNDS)})
                ]
                break
    return (
        tuple(dict.fromkeys(size for sizes, _ in modes for size in sizes)),
        tuple(dict.fromkeys(bounds for _, ranges in modes for bounds in ranges)),
    )
