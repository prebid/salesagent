"""What core hands an ad server adapter to traffic one creative: the carrier and the asset it rides in.

A leaf: it imports only ``adcp``, ``pydantic`` and the standard library, so the reader
(``src/core/creative_to_traffic.py``) and every adapter can import it without importing each
other.

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
"""

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from adcp.types.domains.core.assets.asset_union import MarkupType as LibraryMarkupType
from pydantic import BaseModel, ConfigDict, Field

_FROZEN = ConfigDict(frozen=True, extra="forbid")

MediaType = Literal["image", "video", "audio"]


class TrafficSize(BaseModel):
    """One width and height, in pixels, the creative may be trafficked at."""

    model_config = _FROZEN

    width: int
    height: int


class TrafficSizeRange(BaseModel):
    """A responsive option's accepted bounds, in pixels. An absent bound does not constrain."""

    model_config = _FROZEN

    min_width: int | None = None
    max_width: int | None = None
    min_height: int | None = None
    max_height: int | None = None


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
    #: The zip's bytes, fetched by core before anything is booked for an adapter that uploads
    #: them (``AdapterCapabilities.uploads_html5_bundles``); None for every other adapter. Kept
    #: out of every dump and repr: a zip belongs in neither a stored row nor a log line.
    bundle: bytes | None = Field(default=None, exclude=True, repr=False)


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
    """One creative as an adapter traffics it: its kind, the representation the buyer sent, its sizes.

    ``sizes`` and ``size_ranges`` are what the creative may be trafficked at. ``option_sizes``
    and ``option_size_ranges`` are what its format option accepts, which for a media file is
    more than the file's own size: an adapter refusing that size tells from them whether
    another size the option accepts would serve. Both pairs empty means "any size". Required,
    so a carrier built without its option cannot read as one accepting any size.
    """

    model_config = _FROZEN

    creative_id: str
    format_kind: str
    representation: Representation
    sizes: tuple[TrafficSize, ...] = ()
    size_ranges: tuple[TrafficSizeRange, ...] = ()
    option_sizes: tuple[TrafficSize, ...]
    option_size_ranges: tuple[TrafficSizeRange, ...]
    clickthrough_url: str | None = None
    impression_tracker_urls: tuple[str, ...] = ()


#: Each package's creatives as core read them for it, keyed by package id: what an adapter's
#: pre-booking check and its booking are handed, the same value for both.
CreativesByPackage = Mapping[str, Sequence[CreativeToTraffic]]


class PackageAssignmentDict(TypedDict):
    """One package a creative is assigned to, as an adapter is handed it."""

    package_id: str
    weight: int
    # The package's line item in the ad server (package_config["platform_line_item_id"]),
    # None for a package that has none.
    platform_line_item_id: str | None
    # The creative as it is trafficked on this package: read with the declaration this
    # package's product resolved to, so it states the sizes this package accepts and no other's.
    creative: CreativeToTraffic


class AdapterCreativeAsset(TypedDict):
    """One creative as ``add_creative_assets`` is handed it.

    Each of ``package_assignments`` carries the creative as it is trafficked on that package.
    ``creative`` is the first assignment's, always present, for the adapters that read one.
    """

    creative_id: str
    creative: CreativeToTraffic
    name: str
    package_assignments: list[PackageAssignmentDict]
    # A creative the ad server already holds is placed on the line items, not created again,
    # unless its content changed since, when the ad server's copy is updated keeping its id.
    platform_creative_id: str | None
    platform_creative_outdated: bool
    # The carrier's click URL, for the adapters that do not read the carrier yet.
    click_url: str | None
    delivery_settings: NotRequired[dict[str, Any]]
