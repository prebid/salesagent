"""What core hands an ad server adapter to traffic one creative, read once from the stored row.

RFC #2529 section 3: "``CreativeToTraffic`` carries the canonical kind and its representation as
the pinned spec defines it ... Core never infers a kind from a URL or defaults one, and never
treats a preview page as serving content." The advertiser is not carried yet: it still comes
from the principal (#2555).

The representation is the asset the buyer sent, named in the spec's own words (AdCP 3.2.7):

* ``core/assets/display-tag-asset.json``: "``delivery_type`` distinguishes a single ad-request
  URL, byte-preserved inline markup, and a paired redirect whose ad-request and click-through
  URLs MUST travel together." Its inline ``markup``: "Receivers MUST preserve its bytes".
* ``docs/creative/channels/display.mdx``: a ``tag_url`` is "One ``url`` asset with
  ``url_type: "ad_request"``, or a ``display_tag`` asset with ``delivery_type: "tag_url"``".
* ``core/assets/zip-asset.json``: an HTML5 bundle, which "Receivers unpack ... and serve".
* ``core/assets/html-asset.json``: "Inline HTML content asset", required ``content`` only.

Which one a creative carries is read off the asset's own ``asset_type`` (and, for a URL, the
url-asset ladder), never off the kind alone: the reference creative agent declares its
``display_*_html`` formats as kind ``html5`` while their one asset is inline ``html``.

Sizes come from the product's format option (``width``/``height``, or ``sizes[]``), else the
legacy format's primary render, else a media asset's own dimensions. A responsive option, or
none, states no size, and the carrier then holds none: which size to traffic is the adapter's
decision against its own inventory.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal

from adcp.types.domains.core.assets.display_tag_asset import MarkupType as LibraryMarkupType
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.core.format_satisfaction import canonical_slot_asset_ids, declaration_slot_asset_ids, declared_params
from src.core.helpers.creative_helpers import (
    extract_click_url,
    extract_impression_tracker_url,
    extract_media_url_and_dimensions,
    media_asset_requires_dimensions,
    url_asset_identification,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")

MediaType = Literal["image", "video", "audio"]

#: The media types, in the order a kind's declared slots are asked for one.
_MEDIA_TYPES: dict[str, MediaType] = {"image": "image", "video": "video", "audio": "audio"}


class TrafficSize(BaseModel):
    """One width and height, in pixels, the creative may be trafficked at."""

    model_config = _FROZEN

    width: int
    height: int


class MediaRepresentation(BaseModel):
    """A hosted image, video or audio file, served as the file."""

    model_config = _FROZEN

    type: Literal["media"] = "media"
    asset_type: MediaType
    url: str


class HtmlRepresentation(BaseModel):
    """Inline HTML (``core/assets/html-asset.json``), served as its ``content``."""

    model_config = _FROZEN

    type: Literal["html"] = "html"
    content: str


class Html5BundleRepresentation(BaseModel):
    """A seller-hosted HTML5 zip bundle (``core/assets/zip-asset.json``) and its optional backup image."""

    model_config = _FROZEN

    type: Literal["html5_bundle"] = "html5_bundle"
    url: str
    backup_image_url: str | None = None


class TagUrlRepresentation(BaseModel):
    """A third-party ad-request URL the ad server invokes at impression time."""

    model_config = _FROZEN

    type: Literal["tag_url"] = "tag_url"
    url: str


class InlineMarkupRepresentation(BaseModel):
    """Third-party tag markup, preserved byte for byte, with its markup type."""

    model_config = _FROZEN

    type: Literal["inline_markup"] = "inline_markup"
    markup: str
    markup_type: LibraryMarkupType


class PairedRedirectRepresentation(BaseModel):
    """An ad-request URL and its click-through URL, one value: "The pair MUST NOT be split"."""

    model_config = _FROZEN

    type: Literal["paired_redirect"] = "paired_redirect"
    ad_request_url: str
    clickthrough_url: str


Representation = Annotated[
    MediaRepresentation
    | HtmlRepresentation
    | Html5BundleRepresentation
    | TagUrlRepresentation
    | InlineMarkupRepresentation
    | PairedRedirectRepresentation,
    Field(discriminator="type"),
]


class CreativeToTraffic(BaseModel):
    """One creative as an adapter traffics it: its kind, the representation the buyer sent, its sizes."""

    model_config = _FROZEN

    creative_id: str
    format_kind: str
    representation: Representation
    sizes: tuple[TrafficSize, ...] = ()
    clickthrough_url: str | None = None
    impression_tracker_urls: tuple[str, ...] = ()


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
    format_spec: Any | None = None,
    declarations: Sequence[Any] = (),
) -> CreativeToTraffic | TrafficRefusal:
    """The ONE reader of a stored creative for trafficking. Pure: it fetches and writes nothing.

    Args:
        creative_id: the creative's id.
        creative_data: the stored ``Creative.data`` document.
        kind: the creative's canonical ``format_kind`` (``CreativeFormatIdentity.kind``).
        format_spec: the legacy format declaration its retained reference resolved to, if any.
        declarations: the product format declarations the creative resolved to, if known.
    """
    data = dict(creative_data)
    assets = _assets(data)
    declared_sizes = _declared_sizes(declarations, format_spec)
    try:
        representation = _representation(data, assets, kind, format_spec, declarations)
    except ValidationError:
        # A stored asset of a recognized type whose members do not fit it, for example a
        # markup_type outside the pinned enum, carries nothing an adapter can serve.
        representation = None
    if representation is None:
        return TrafficRefusal(creative_id, TrafficRefusalReason.NO_SERVING_CONTENT)

    sizes = declared_sizes
    if isinstance(representation, MediaRepresentation):
        _url, width, height = extract_media_url_and_dimensions(data, format_spec, kind, declarations)
        if width and height:
            sizes = (TrafficSize(width=width, height=height),)
        elif not sizes and media_asset_requires_dimensions(data, format_spec, kind, declarations):
            return TrafficRefusal(creative_id, TrafficRefusalReason.NO_DIMENSIONS)

    impression_tracker = extract_impression_tracker_url(data, format_spec)
    return CreativeToTraffic(
        creative_id=creative_id,
        format_kind=kind,
        representation=representation,
        sizes=sizes,
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
        return _generated_media(data, kind)

    if tag := _first_of_type(assets, "display_tag"):
        return _display_tag(tag)
    if bundle := _first_of_type(assets, "zip"):
        return Html5BundleRepresentation(url=bundle["url"], backup_image_url=_backup_image_url(assets, bundle))
    if html := _first_of_type(assets, "html"):
        return HtmlRepresentation(content=html["content"]) if html.get("content") else None
    if ad_request_url := _ad_request_url(data, assets, kind, format_spec, declarations):
        return TagUrlRepresentation(url=ad_request_url)
    if kind in _NON_MEDIA_KINDS:
        # The kind is served as markup, a bundle or a tag, and the creative carries none of
        # them. Its root ``url`` is not a substitute: for these kinds sync wrote the creative
        # agent's preview page there.
        return None

    url, _width, _height = extract_media_url_and_dimensions(data, format_spec, kind, declarations)
    if not url:
        return None
    return MediaRepresentation(asset_type=_media_asset_type(assets, kind, declarations), url=str(url))


#: Kinds whose serving content is never a plain media file.
_NON_MEDIA_KINDS = frozenset({"display_tag", "html5"})


def _display_tag(asset: Mapping[str, Any]) -> Representation | None:
    """One ``display_tag`` asset as its delivery type's arm."""
    delivery_type = asset.get("delivery_type")
    if delivery_type == "tag_url":
        return TagUrlRepresentation(url=asset["url"])
    if delivery_type == "inline_markup":
        return InlineMarkupRepresentation(markup=asset["markup"], markup_type=asset["markup_type"])
    if delivery_type == "paired_redirect":
        return PairedRedirectRepresentation(
            ad_request_url=asset["ad_request_url"], clickthrough_url=asset["clickthrough_url"]
        )
    return None


def _ad_request_url(
    data: dict[str, Any],
    assets: Mapping[str, Mapping[str, Any]],
    kind: str,
    format_spec: Any | None,
    declarations: Sequence[Any],
) -> str | None:
    """A legacy ``tag_url``: a url asset invoked as an ad request.

    By its own ``url_type`` (the url-asset ladder), or, for a ``display_tag`` creative, by
    sitting in the kind's url slot: ``formats/canonical/display_tag.json`` says "``tag_url``
    MUST use ``url_type: ad_request``", so the format states the mechanism the asset omits.
    """
    mechanisms, _unidentified = url_asset_identification(data, format_spec)
    slot_ids: frozenset[str] = frozenset()
    if kind == "display_tag":
        slot_ids = canonical_slot_asset_ids(kind, {"url"}).union(
            *(declaration_slot_asset_ids(declaration, {"url"}) for declaration in declarations)
        )
    for asset_id, asset in assets.items():
        if asset.get("asset_type") != "url" or not asset.get("url"):
            continue
        if mechanisms.get(asset_id) == "ad_request" or (asset_id in slot_ids and "url_type" not in asset):
            return str(asset["url"])
    return None


def _generated_media(data: dict[str, Any], kind: str) -> MediaRepresentation | None:
    """A generative creative's build output: the file ``build_creative`` produced, read as its kind's media.

    The output is what is served. Its URL is the one sync stored from the build output
    (``output_format.url``), never a preview, and a creative with no build output has nothing
    to serve yet.
    """
    output = data.get("output_format")
    url = output.get("url") if isinstance(output, Mapping) else None
    if not url:
        return None
    return MediaRepresentation(asset_type=_media_asset_type({}, kind, ()), url=str(url))


def _media_asset_type(assets: Mapping[str, Mapping[str, Any]], kind: str, declarations: Sequence[Any]) -> MediaType:
    """The media type the creative's file is: its own asset's, else the first its kind or option declares."""
    for asset in assets.values():
        own = _MEDIA_TYPES.get(str(asset.get("asset_type")))
        if own and asset.get("url"):
            return own
    for media_type in _MEDIA_TYPES.values():
        if canonical_slot_asset_ids(kind, {media_type}) or any(
            declaration_slot_asset_ids(declaration, {media_type}) for declaration in declarations
        ):
            return media_type
    return "image"


def _backup_image_url(assets: Mapping[str, Mapping[str, Any]], bundle: Mapping[str, Any]) -> str | None:
    """The html5 kind's ``backup_image`` slot, else the zip asset's own ``backup_image_url``."""
    backup = assets.get("backup_image")
    if backup and backup.get("asset_type") == "image" and backup.get("url"):
        return str(backup["url"])
    return str(bundle["backup_image_url"]) if bundle.get("backup_image_url") else None


def _first_of_type(assets: Mapping[str, Mapping[str, Any]], asset_type: str) -> Mapping[str, Any] | None:
    """The first asset of *asset_type*, in asset-id order so two reads of one creative agree."""
    for asset_id in sorted(assets):
        if assets[asset_id].get("asset_type") == asset_type:
            return assets[asset_id]
    return None


def _assets(data: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The manifest's single-valued assets. A repeatable slot stores a list, which no representation reads."""
    raw = data.get("assets")
    if not isinstance(raw, Mapping):
        return {}
    return {str(asset_id): asset for asset_id, asset in raw.items() if isinstance(asset, Mapping)}


def _declared_sizes(declarations: Sequence[Any], format_spec: Any | None) -> tuple[TrafficSize, ...]:
    """The sizes the format accepts: each option's ``width``/``height`` or ``sizes[]``, else the legacy render."""
    sizes: list[TrafficSize] = []
    for declaration in declarations:
        params = declared_params(declaration)
        if params.get("width") and params.get("height"):
            sizes.append(TrafficSize(width=int(params["width"]), height=int(params["height"])))
        for size in params.get("sizes") or ():
            sizes.append(TrafficSize(width=int(size["width"]), height=int(size["height"])))
    if not sizes and format_spec is not None:
        sizes.extend(_render_sizes(format_spec))
    return tuple(dict.fromkeys(sizes))


def _render_sizes(format_spec: Any) -> list[TrafficSize]:
    """A legacy format's primary render, when it states a fixed width and height."""
    for render in getattr(format_spec, "renders", None) or ():
        dimensions = getattr(render, "dimensions", None)
        if getattr(render, "role", None) != "primary" or dimensions is None:
            continue
        width, height = getattr(dimensions, "width", None), getattr(dimensions, "height", None)
        if width and height:
            return [TrafficSize(width=int(width), height=int(height))]
    return []
