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
* An ``ImageRedirectCreative`` needs a ``destinationUrl``. The v202602 reference says
  ``HasDestinationUrlCreative.destinationUrl`` "is required unless the destinationUrlType is set
  to DestinationUrlType.NONE", but the configured test network, probed on 2026-11-20 with one
  300x250 image and no click-through per call, refused every variant: ``destinationUrlType``
  NONE with ``CreativeError.DESTINATION_URL_TYPE_NOT_SUPPORTED @ [0].destinationUrlType``;
  neither field, CLICK_TO_WEB with no URL, and an empty ``destinationUrl`` each with
  ``RequiredError.REQUIRED @ [0].destinationUrl``. So an image with no click-through is refused
  at plan time (``GamRefusalReason.NO_CLICKTHROUGH``).

The plan leaves out ``advertiserId`` and ``name``: the creatives manager owns both, as it does
today. An html5 bundle's bytes come on the carrier (``Html5BundleRepresentation.bundle``): core
fetches the bundle from the buyer's host before anything is booked, so neither the plan nor the
upload fetches anything.
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
from src.services.gam_product_config_service import gam_format_family


@dataclass(frozen=True)
class GamCreativePlan:
    """The GAM creative to create for one creative, and the sizes its associations carry.

    ``creative`` is the GAM ``Creative`` without ``advertiserId`` and ``name``.
    ``lica_sizes`` are GAM ``Size`` objects for ``LineItemCreativeAssociation.sizes``, the
    creative's own size first; empty means the association states none.
    """

    creative: dict[str, Any]
    lica_sizes: tuple[dict[str, int], ...] = ()


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

    NO_CLICKTHROUGH = "no_clickthrough"
    """It is an image with no click-through, which a GAM image creative requires."""

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
        * ``CREATIVE_MISSING_CLICK_URL``: "A submitted creative that requires a destination URL
          does not provide one ... Recovery: correctable (add the required destination URL and
          resubmit the creative)" -- an image, whose GAM creative needs a click-through.
        """
        if self is GamRefusalReason.NOT_TRAFFICKED:
            return ErrorCode.UNSUPPORTED_FEATURE
        if self is GamRefusalReason.NO_CLICKTHROUGH:
            return ErrorCode.CREATIVE_MISSING_CLICK_URL
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
    """The sizes of GAM ``CreativePlaceholder`` objects (dicts or Zeep objects), each once, in canonical order.

    Canonical is ascending ``(width, height)``, never the order the placeholders arrive in. The
    pre-booking check reads the placeholders a line item will be written with, and the upload
    the ones GAM answers for it, and GAM does not answer them in the order written (R9's e2e
    run 025d613f wrote [300x600, 728x90] and read [728x90, 300x600]), so a plan reading arrival
    order could approve one size and create another. Both plan through this order.
    """
    sizes = {
        TrafficSize(width=int(placeholder["size"]["width"]), height=int(placeholder["size"]["height"]))
        for placeholder in placeholders
    }
    return tuple(sorted(sizes, key=lambda size: (size.width, size.height)))


def plan_gam_creative(
    ctt: CreativeToTraffic, placeholder_sizes: Sequence[TrafficSize]
) -> GamCreativePlan | GamCreativeRefusal:
    """The GAM creative for *ctt* on line items taking *placeholder_sizes*, or why there is none.

    Size: the candidates are the declared ``sizes`` a placeholder takes, in the order declared,
    or the placeholders inside a declared ``size_ranges`` entry (every stated bound
    inclusive); with neither declared, every placeholder. Placeholders are taken in the order
    given, which in production is :func:`placeholder_sizes`' canonical one. The first candidate
    is the creative's size. With more than
    one, ``lica_sizes`` is every candidate, the creative's own first: the association's sizes
    "override" ``Creative.size``, and listing its own keeps it served at that size whether GAM
    adds them or replaces it. With one, the association states none. No candidate is a
    refusal: there is no default size.
    """
    representation = ctt.representation
    if gam_format_family(ctt.format_kind) != "display" or (
        isinstance(representation, MediaRepresentation) and representation.asset_type != "image"
    ):
        # Only the display family is planned. A native kind is a GAM native style over a
        # creative template (PR #2445); audio has no GAM creative; video needs a duration
        # (VideoRedirectCreative), which the carrier does not hold; a kind GAM has no family for
        # names no GAM creative type.
        return GamCreativeRefusal(ctt.creative_id, GamRefusalReason.NOT_TRAFFICKED, ctt.format_kind)

    sizes = _candidate_sizes(ctt, placeholder_sizes)
    if isinstance(sizes, GamCreativeRefusal):
        return sizes
    size, *others = candidates = tuple({"width": s.width, "height": s.height} for s in sizes)
    lica_sizes = candidates if others else ()

    creative: dict[str, Any] = {"size": size}
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
        assert representation.bundle is not None, "core fetches an html5 bundle before GAM is handed its carrier"
        creative.update(
            xsi_type="Html5Creative",
            html5Asset={"fileName": f"{ctt.creative_id}.zip", "assetByteArray": representation.bundle},
        )
    elif not ctt.clickthrough_url:
        return GamCreativeRefusal(ctt.creative_id, GamRefusalReason.NO_CLICKTHROUGH)
    else:
        creative.update(
            xsi_type="ImageRedirectCreative",
            imageUrl=representation.url,
            destinationUrl=substitute_macros(ctt.clickthrough_url),
        )

    if ctt.impression_tracker_urls:
        creative["thirdPartyImpressionTrackingUrls"] = substitute_tracking_urls(list(ctt.impression_tracker_urls))
    return GamCreativePlan(creative=creative, lica_sizes=lica_sizes)


def plan_or_refuse(
    ctt: CreativeToTraffic, placeholders: Iterable[Any], package_id: str | None = None
) -> GamCreativePlan | ErrorProblem:
    """The GAM creative *ctt* becomes on a line item with these *placeholders*, or the problem refusing it.

    The one planning call, per line item: the pre-booking check (``GoogleAdManager.validate_media_buy_request``)
    makes it against the placeholders the line item will be created with, and the upload
    (``GAMCreativesManager.add_creative_assets``) against the ones GAM holds, so the plan the
    check approved is the plan the upload executes. A refusal is the problem it is, naming
    *package_id*; core builds the error (``exceptions.problems_refusal``).
    """
    plan = plan_gam_creative(ctt, placeholder_sizes(placeholders))
    return plan.problem(package_id) if isinstance(plan, GamCreativeRefusal) else plan


def placed_sizes(creatives: Iterable[CreativeToTraffic], sizes: Sequence[TrafficSize]) -> set[TrafficSize]:
    """Those of *sizes* some creative in *creatives* would be planned at, by the plan's own matching."""
    candidates = tuple(dict.fromkeys(sizes))
    return {size for ctt in creatives for size in _placed(ctt.sizes, ctt.size_ranges, candidates)}


def _candidate_sizes(
    ctt: CreativeToTraffic, placeholder_sizes: Sequence[TrafficSize]
) -> tuple[TrafficSize, ...] | GamCreativeRefusal:
    """The sizes *ctt* may be trafficked at on these placeholders, in order, or the refusal.

    With none, the refusal says whether the buyer can fix it: a size the creative's OPTION
    accepts that a placeholder takes is one a resized creative would serve at; with no such
    size, no creative the option accepts can serve on these line items.
    """
    placeholders = tuple(dict.fromkeys(placeholder_sizes))
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
    """The placeholders one size mode takes: the declared sizes a placeholder takes, in declared order;
    else the placeholders inside the bounds; with neither stated, every placeholder.

    One mode, never both: ``formats/canonical/display_tag.json``, ``image.json`` and ``html5.json``
    reject a declaration combining them ("Exactly one of: (a) fixed ... (b) multi-size ... (c)
    responsive ... (d) none"), and the reader takes a carrier's sizes from one declaration or from
    the file's own size.
    """
    if sizes:
        return tuple(size for size in sizes if size in placeholders)
    if size_ranges:
        return tuple(size for size in placeholders if any(_within(size, bounds) for bounds in size_ranges))
    return placeholders


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
