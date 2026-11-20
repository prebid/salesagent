"""What one creative becomes in Google Ad Manager: the GAM creative and its association sizes.

``plan_gam_creative`` maps core's ``CreativeToTraffic`` to the GAM creative to create and the
``sizes`` its line item creative associations carry, or refuses it. It is pure: no client
call, no fetch, no write. RFC #2529 section 4: "Inline markup becomes a ThirdPartyCreative
whose snippet is the markup byte for byte; a tag URL, a ThirdPartyCreative whose snippet is a
script tag calling it; a paired redirect, an ImageRedirectCreative; an html5 bundle, an
Html5Creative. A GAM creative has one size and one type for life ... a size that matches none
is refused at plan time, with no default."

GAM facts are Ad Manager API v202602 (googleads 49.0.0 defaults to it):

* ``LineItemCreativeAssociation.sizes``: "Overrides the value set for Creative.size, which
  allows the creative to be served to ad units that would otherwise not be compatible for its
  actual size. This value is optional."
* ``Html5Creative`` requires ``advertiserId``, ``name``, ``size`` and ``html5Asset``; the
  asset's ``assetByteArray`` "must be a zip bundle" and its ``fileName`` "must have a zip
  extension". It is not a ``HasDestinationUrlCreative``, so it has no ``destinationUrl``.
* ``HasDestinationUrlCreative.destinationUrl`` "is required unless the destinationUrlType is
  set to DestinationUrlType.NONE".

The plan leaves out ``advertiserId`` and ``name``: the creatives manager owns both, as it does
today. It also leaves out the html5 bundle's bytes: the manager fetches ``html5_bundle_url``
through ``src.core.security.outbound_http.asend`` and fills them with
:meth:`GamCreativePlan.with_html5_bundle`.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.adapters.gam.utils.macros import substitute_macros, substitute_tracking_urls
from src.core.errors.codes import ErrorCode, ErrorCodeT
from src.core.errors.details import ErrorProblem
from src.core.traffic_carrier import (
    CreativeToTraffic,
    Html5BundleRepresentation,
    HtmlRepresentation,
    InlineMarkupRepresentation,
    MediaRepresentation,
    PairedRedirectRepresentation,
    TagUrlRepresentation,
    TrafficSize,
    TrafficSizeRange,
)

#: Canonical kinds GAM is not handed through this plan. ``native_in_feed`` is a GAM native
#: style with a creative template, which PR #2445 owns.
_KINDS_NOT_PLANNED = frozenset({"native_in_feed"})


@dataclass(frozen=True)
class GamCreativePlan:
    """The GAM creative to create for one creative, and the sizes its associations carry.

    ``creative`` is the GAM ``Creative`` without ``advertiserId`` and ``name``.
    ``lica_sizes`` are GAM ``Size`` objects for ``LineItemCreativeAssociation.sizes``, the
    creative's own size first; empty means the association states none. ``html5_bundle_url`` is set for an ``Html5Creative``
    only: its zip bytes are not in ``creative`` until :meth:`with_html5_bundle` fills them.
    """

    creative: dict[str, Any]
    lica_sizes: tuple[dict[str, int], ...] = ()
    html5_bundle_url: str | None = None

    def with_html5_bundle(self, bundle: bytes) -> dict[str, Any]:
        """The ``Html5Creative`` with the fetched zip *bundle* as its ``html5Asset.assetByteArray``."""
        assert self.html5_bundle_url is not None, "only an Html5Creative plan carries a bundle to fill"
        return {**self.creative, "html5Asset": {**self.creative["html5Asset"], "assetByteArray": bundle}}


class GamRefusalReason(StrEnum):
    """Why a creative has no GAM creative."""

    NOT_TRAFFICKED = "not_trafficked"
    """Its kind or representation is one this seller does not traffic through GAM."""

    NO_PLACEHOLDER = "no_placeholder"
    """Its line items state no creative size at all."""

    NO_MATCHING_SIZE = "no_matching_size"
    """None of the sizes it accepts is a size its line items take, and another size its option accepts is."""

    NO_OPTION_SIZE_PLACED = "no_option_size_placed"
    """No size its option accepts is a size its line items take."""

    @property
    def code(self) -> ErrorCodeT:
        """The adapter-independent code the refusal is, from the one code table (enums/error-code.json).

        * ``UNSUPPORTED_FEATURE``: "A requested feature or field is not supported by this seller".
        * ``CREATIVE_SIZE_MISMATCH``: "The submitted creative dimensions do not match any size
          accepted by the selected packages ... Recovery: correctable (resize or replace the
          creative with a size accepted by every selected package that may serve it)" -- which
          another size the option accepts would satisfy.
        * ``CONFIGURATION_ERROR``: "the buyer cannot fix it, retrying will not help, and
          reporting to the seller's operator is the only remediation" -- a line item stating no
          size, or one taking no size the option accepts, which no creative the buyer may send
          can serve.
        """
        if self is GamRefusalReason.NOT_TRAFFICKED:
            return ErrorCode.UNSUPPORTED_FEATURE
        if self is GamRefusalReason.NO_MATCHING_SIZE:
            return ErrorCode.CREATIVE_SIZE_MISMATCH
        return ErrorCode.CONFIGURATION_ERROR


@dataclass(frozen=True)
class GamCreativeRefusal:
    """A creative GAM is not handed, and why. :meth:`problem` classifies it; core raises the refusal."""

    creative_id: str
    reason: GamRefusalReason
    #: What was refused: the kind, or the sizes and bounds the creative accepts.
    rejected_value: str | list[str] | None = None
    #: The sizes the option accepts that the line items take, for a size refusal.
    accepted_values: tuple[str, ...] = ()

    def problem(self, package_id: str | None = None) -> ErrorProblem:
        """The refusal as the problem it is: its creative, the package it is refused on, and its code.

        ``CONFIGURATION_ERROR`` names no values: the pin defines no details for it, and the
        line items' sizes are not sizes the buyer could send.
        """
        configuration = self.reason.code == ErrorCode.CONFIGURATION_ERROR
        return ErrorProblem(
            code=self.reason.code,
            subject_type="creative",
            subject_id=self.creative_id,
            package_id=package_id,
            rejected_value=None if configuration else self.rejected_value,
            accepted_values=list(self.accepted_values) or None,
        )


def placeholder_sizes(placeholders: Iterable[Any]) -> tuple[TrafficSize, ...]:
    """The sizes of GAM ``CreativePlaceholder`` objects (dicts or Zeep objects), in order, each once."""
    sizes = (
        TrafficSize(width=int(placeholder["size"]["width"]), height=int(placeholder["size"]["height"]))
        for placeholder in placeholders
    )
    return tuple(dict.fromkeys(sizes))


def plan_gam_creative(
    ctt: CreativeToTraffic, placeholder_sizes: Sequence[TrafficSize]
) -> GamCreativePlan | GamCreativeRefusal:
    """The GAM creative for *ctt* on line items taking *placeholder_sizes*, or why there is none.

    Size: the candidates are the declared ``sizes`` a placeholder takes, plus the placeholders
    inside a declared ``size_ranges`` entry (every stated bound inclusive); with neither
    declared, every placeholder. The first candidate is the creative's size. With more than
    one, ``lica_sizes`` is every candidate, the creative's own first: the association's sizes
    "override" ``Creative.size``, and listing its own keeps it served at that size whether GAM
    adds them or replaces it. With one, the association states none. No candidate, or no
    placeholder, is a refusal: there is no default size.
    """
    representation = ctt.representation
    if ctt.format_kind in _KINDS_NOT_PLANNED or (
        isinstance(representation, MediaRepresentation) and representation.asset_type != "image"
    ):
        # Audio has no GAM creative. Video needs a duration (VideoRedirectCreative), which the
        # carrier does not hold.
        return GamCreativeRefusal(ctt.creative_id, GamRefusalReason.NOT_TRAFFICKED, ctt.format_kind)

    sizes = _candidate_sizes(ctt, placeholder_sizes)
    if isinstance(sizes, GamCreativeRefusal):
        return sizes
    size, *others = candidates = tuple({"width": s.width, "height": s.height} for s in sizes)
    lica_sizes = candidates if others else ()

    creative: dict[str, Any] = {"size": size}
    bundle_url = None
    if isinstance(representation, InlineMarkupRepresentation):
        creative.update(xsi_type="ThirdPartyCreative", snippet=representation.markup)
    elif isinstance(representation, HtmlRepresentation):
        creative.update(xsi_type="ThirdPartyCreative", snippet=representation.content)
    elif isinstance(representation, TagUrlRepresentation):
        creative.update(
            xsi_type="ThirdPartyCreative", snippet=f'<script src="{html.escape(representation.url)}"></script>'
        )
    elif isinstance(representation, PairedRedirectRepresentation):
        # The pair travels as one value: neither URL is rewritten.
        creative.update(
            xsi_type="ImageRedirectCreative",
            imageUrl=representation.ad_request_url,
            destinationUrl=representation.clickthrough_url,
        )
    elif isinstance(representation, Html5BundleRepresentation):
        creative.update(xsi_type="Html5Creative", html5Asset={"fileName": f"{ctt.creative_id}.zip"})
        bundle_url = representation.url
    else:
        creative.update(xsi_type="ImageRedirectCreative", imageUrl=representation.url, **_destination(ctt))

    if ctt.impression_tracker_urls:
        creative["thirdPartyImpressionTrackingUrls"] = substitute_tracking_urls(list(ctt.impression_tracker_urls))
    return GamCreativePlan(creative=creative, lica_sizes=lica_sizes, html5_bundle_url=bundle_url)


def _destination(ctt: CreativeToTraffic) -> dict[str, str]:
    """An image's click-through, with AdCP macros in GAM's spelling; none states ``NONE``."""
    if ctt.clickthrough_url:
        return {"destinationUrl": substitute_macros(ctt.clickthrough_url)}
    return {"destinationUrlType": "NONE"}


def _candidate_sizes(
    ctt: CreativeToTraffic, placeholder_sizes: Sequence[TrafficSize]
) -> tuple[TrafficSize, ...] | GamCreativeRefusal:
    """The sizes *ctt* may be trafficked at on these placeholders, in order, or the refusal.

    With none, the refusal says whether the buyer can fix it: a size the creative's OPTION
    accepts that a placeholder takes is one a resized creative would serve at; with no such
    size, no creative the option accepts can serve on these line items.
    """
    placeholders = tuple(dict.fromkeys(placeholder_sizes))
    if not placeholders:
        return GamCreativeRefusal(ctt.creative_id, GamRefusalReason.NO_PLACEHOLDER)
    candidates = _placed(ctt.sizes, ctt.size_ranges, placeholders)
    if candidates:
        return candidates
    placed_by_option = _placed(ctt.option_sizes, ctt.option_size_ranges, placeholders)
    if not placed_by_option:
        return GamCreativeRefusal(ctt.creative_id, GamRefusalReason.NO_OPTION_SIZE_PLACED)
    return GamCreativeRefusal(
        ctt.creative_id,
        GamRefusalReason.NO_MATCHING_SIZE,
        _spelled(ctt.sizes) + [_spelled_range(bounds) for bounds in ctt.size_ranges],
        tuple(_spelled(placed_by_option)),
    )


def _placed(
    sizes: Sequence[TrafficSize], size_ranges: Sequence[TrafficSizeRange], placeholders: tuple[TrafficSize, ...]
) -> tuple[TrafficSize, ...]:
    """The placeholders these sizes and bounds take, declared sizes first; with neither stated, every placeholder."""
    if not sizes and not size_ranges:
        return placeholders
    declared = [size for size in sizes if size in placeholders]
    in_range = [size for size in placeholders if any(_within(size, bounds) for bounds in size_ranges)]
    return tuple(dict.fromkeys([*declared, *in_range]))


def _within(size: TrafficSize, bounds: TrafficSizeRange) -> bool:
    """Whether *size* is inside *bounds*, every stated bound inclusive and an absent one unconstraining."""
    return (
        (bounds.min_width is None or size.width >= bounds.min_width)
        and (bounds.max_width is None or size.width <= bounds.max_width)
        and (bounds.min_height is None or size.height >= bounds.min_height)
        and (bounds.max_height is None or size.height <= bounds.max_height)
    )


def _spelled(sizes: Iterable[TrafficSize]) -> list[str]:
    """Each size as ``WIDTHxHEIGHT``."""
    return [f"{size.width}x{size.height}" for size in sizes]


def _spelled_range(bounds: TrafficSizeRange) -> str:
    """A range as its stated bounds, ``width>=300 width<=728 height>=90``; an absent bound is left out."""
    stated = (
        ("width>=", bounds.min_width),
        ("width<=", bounds.max_width),
        ("height>=", bounds.min_height),
        ("height<=", bounds.max_height),
    )
    return " ".join(f"{label}{value}" for label, value in stated if value is not None)
