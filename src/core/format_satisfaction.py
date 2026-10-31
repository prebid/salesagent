"""Does a named format satisfy what a product declares? Asked here, and only here.

ONE function for the whole question, because the four gates it replaces had four
answers to it. Each read ``product.format_ids`` through
:func:`src.core.format_resolver.product_format_identities`, compared legacy identities,
and did NOTHING AT ALL for a product declaring ``format_options[]`` -- and they did not
even agree on that nothing: the create gate SKIPPED a creative whose format carried no
legacy assertion while the update and assignment gates REFUSED one. The two comments at
``media_buy_create.py`` that recorded the skip as deliberate said it was pending this
module.

THE AUTHORITY is ``docs/creative/formats.mdx`` § Matching and satisfaction (AdCP,
the pinned version -- ``adcp.get_adcp_spec_version()``), which states the relation as five
rules:

* Exact dimensions and durations must match.
* A requested range must be contained within the accepted range; overlap is insufficient.
* Required slots must be present with compatible asset types.
* An exact publisher claim must match both ``publisher_domain`` and ``format_option_id``.
* Equal ``format_kind`` values alone do not prove compatibility.

and ``core/product-format-declaration.json``'s ``seller_preference``, which makes
``format_options[]`` a CLOSED SET: "anything outside the list is rejected at
``create_media_buy`` regardless of preference". That closed-set step is the SDK's --
``adcp.canonical_formats.format_options.validate_format_kind_in_options`` -- and it is a
PER-PRODUCT set, not a global vocabulary, so a kind in the pinned enum but absent from
this product's declarations is still refused.

Graded by ``protocols/media-buy/scenarios/canonical_formats.yaml`` (at the pin), whose
``canonical_create_satisfaction`` phase is this module's conformance contract. Its
``reject_bare_image_selector_for_fixed_mrec`` step is the fifth rule made concrete: a
selector naming ``format_kind: image`` and no dimensions does not satisfy a product that
fixes 300x250, and the refusal is ``UNSUPPORTED_FEATURE``.

A PACKAGE's selector may address a format several ways at once, and
``media-buy/package-request.json``'s "Format selector contract (normative)" states the
four steps a receiver runs over them: resolve each route independently, derive which
product declarations each resolved route SELECTS, refuse a disagreement with
``CONFLICTING_SELECTORS``, and only then apply precedence. :class:`SelectorResolution` is
those four steps, one property each; :func:`satisfies` is the single-route entry the four
creative gates use.

The same section of ``formats.mdx`` then states a sixth rule, about the package rather than
the route: "For a product with multiple included placements, the selected format set must
cover every placement." :attr:`SelectorResolution.uncovered` is that rule, asked once the
four steps have selected a declaration.

A CREATIVE IS GRADED ON ITS MANIFEST, not on params. A stored creative declares a kind and
an option, never ``params``; what it states about its dimensions, duration, codecs and file
size is on its ASSETS. ``docs/creative/creative-manifests.mdx`` § Validation is the rule:

  1. Resolve ``format_option_ref`` when present; otherwise select the unambiguous compatible
     ``format_kind`` declaration.
  2. Confirm all required slots are present.
  3. Validate each asset against its declared type and technical constraints.
  4. Check fixed dimensions, durations, codecs, file-size limits, and required disclosures.
  5. Reject unknown or uncovered selections; do not silently relax the target contract.

:func:`creative_satisfies` is that rule, and it runs through the same routes as a selector:
the manifest rides on the route (:attr:`FormatRoute.manifest`), the selected declaration is
graded against it by :func:`_manifest_shortfalls`, and the facts an asset states are put in
the declaration's own vocabulary so :func:`_unsatisfied_params` -- the one matcher -- grades
them. ``formats.mdx``'s fifth rule is what this closes on the creative side: a same-kind
728x90 creative no longer satisfies a product fixing 300x250.

WHAT THIS MODULE DOES NOT DO, deliberately:

* It does not grade a LEGACY creative's manifest against a PRODUCT declaration. ``assets``
  is keyed by the matched declaration's ``params.slots[]`` on the canonical branch
  (creative-manifests.mdx § Basic structure), while a creative naming a ``format_id`` keys
  its assets by that named format's own asset ids, so grading them against a canonical slot
  list would refuse every legacy creative for a vocabulary it never used. At a product, only
  the reference's own parameters are graded (:func:`_legacy_shortfalls`). The manifest itself
  is graded at ``sync_creatives`` against the format it names, as the agent serves it
  (:func:`named_format_manifest_verdict`): its required assets present, each of the type the
  format declares, by the same slot reader as a canonical declaration.
* Required disclosures are not read here: no image, video or audio declaration param states
  one. A seller's disclosure requirement is its ``creative_policy`` (core/creative-policy.json
  ``require_disclosure_metadata``), enforced on the provenance at sync.
* Not every per-kind param is read. Every slot's own constraints are
  (:func:`_slot_constraint_shortfalls`), including the ones a declaration states flat by the
  slot's role (:func:`_effective_slot`), and so are a declared ``aspect_ratio``
  (:func:`_aspect_shortfalls`), an audio spot's loudness and true peak and a required caption
  track (:func:`_level_shortfalls`). So are ``cta_values`` (:func:`_closed_value_shortfalls`,
  CREATIVE_VALUE_NOT_ALLOWED), a carousel's card count and what it states about each card's
  ``media`` (:func:`_card_media_shortfalls`), and a responsive creative's video duration
  bounds, logo aspect ratios and image file-size cap, and a native unit's image file-size cap
  (``max_image_file_size_kb``). Not read: ``om_sdk_required``, which no
  manifest member states (adcontextprotocol/adcp#8627 asks how a seller is meant to validate it).
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from decimal import Decimal, InvalidOperation
from enum import Enum, StrEnum
from functools import lru_cache
from typing import Any, TypeGuard, get_args

from adcp.canonical_formats import formats_are_equivalent, upgrade_legacy_format_id
from adcp.canonical_formats.format_options import FormatKindNotInClosedSetError
from adcp.canonical_formats.projection import project_legacy_format_id
from adcp.types import ErrorCode
from adcp.types.domains.core.format import (
    BaseIndividualAsset as LibraryIndividualAsset,
)  # no stable alias in adcp.types
from adcp.types.domains.core.format_id import FormatReferenceStructuredObject as LibraryFormatId
from adcp.types.domains.core.format_option_ref import FormatOptionReference as LibraryFormatOptionReference
from adcp.types.domains.core.placement import Kind as LibraryPlacementKind
from adcp.types.domains.core.placement import Mode as LibraryPlacementMode
from adcp.types.domains.core.product_format_declaration import (
    ProductFormatDeclaration as LibraryFormatDeclaration,
)
from pydantic import BaseModel, RootModel, TypeAdapter, ValidationError

from src.core.errors.details import (
    CapabilityRefusalDetails,
    CreativeManifestDetails,
    CreativeValueNotAllowedDetails,
    ErrorProblem,
    SelectorConflictDetails,
    SelectorRouteSummary,
)
from src.core.exceptions import (
    AdCPCapabilityNotSupportedError,
    AdCPConflictingSelectorsError,
    AdCPCreativeMissingClickUrlError,
    AdCPCreativeSizeMismatchError,
    AdCPCreativeValidationFailedError,
    AdCPCreativeValueNotAllowedError,
)
from src.core.schemas import Format, FormatOptionIdentity, FormatSelector

logger = logging.getLogger(__name__)

#: ONE declaration type, and it is the one ``Product.format_options`` declares: the
#: SIXTEEN-ARM discriminated union over ``format_kind`` that
#: ``core/product-format-declaration.json`` generates.
#:
#: Both shapes of the column arrive here and this is what makes them one answer. An ORM row
#: holds DOCUMENTS; a converted product holds the union's arms already. Validating both
#: into the union is not bookkeeping -- the arm's ``params`` is the PER-KIND canonical
#: schema, so validating a document applies that schema's own conjuncts (an ``image``
#: declaring ``width`` and no ``height`` is refused HERE) and fills the parameters the
#: schema states by default, which is how a stored document and a served model come to
#: grade identically. Validated per entry, so one unparseable declaration drops itself
#: rather than the product's whole closed set.
_DECLARATION: TypeAdapter[LibraryFormatDeclaration] = TypeAdapter(LibraryFormatDeclaration)


class FormatRefusal(StrEnum):
    """Why a claim did not satisfy, at the granularity the raise sites need.

    One member per rule that can fail, because the buyer's correction differs: an
    unpublished kind means pick another product, an unrouted option ref means name an
    option this product actually publishes, unsatisfied params mean complete the selector,
    an unprojectable legacy reference means this product does not recognise that named
    format at all, a normalizable legacy reference that lands on no published declaration
    means this product does not sell that shape, and a legacy reference outside the
    product's legacy set means a different creative. The wire CODE is not a member here --
    a code belongs to an exception class (``docs/design/error-architecture.md``), and the
    raise site picks it.

    THE MEMBERS PARTITION INTO THE PIN'S TWO STEPS, and :data:`_RESOLUTION_FAILURES` is
    that partition written down. ``media-buy/package-request.json`` separates (1) resolving
    a route to a canonical declaration from (2) deriving which product declarations the
    resolved route then SELECTS, and the two failures carry different codes on a
    multi-route package -- so which step a member belongs to is a property of the member,
    not of the call site.
    """

    KIND_NOT_PUBLISHED = "kind_not_published"
    OPTION_NOT_PUBLISHED = "option_not_published"
    PARAMS_NOT_SATISFIED = "params_not_satisfied"
    LEGACY_UNPROJECTABLE = "legacy_unprojectable"
    LEGACY_NOT_SELECTED = "legacy_not_selected"
    LEGACY_NOT_ACCEPTED = "legacy_not_accepted"
    PLACEMENT_UNCOVERED = "placement_uncovered"
    PLACEMENT_UNRESOLVED = "placement_unresolved"
    MANIFEST_NOT_SATISFIED = "manifest_not_satisfied"
    MANIFEST_AMBIGUOUS = "manifest_ambiguous"
    #: A creative naming BOTH a legacy ``format_id`` and a ``format_option_ref`` whose
    #: reference does not select the declaration the ref names (:func:`creative_satisfies`).
    LEGACY_NOT_OPTION = "legacy_not_option"


#: The two members that are a RESOLUTION failure in ``media-buy/package-request.json``'s
#: step (1), which names them both: "an unresolved ``format_option_ref`` or a legacy
#: ``format_id`` that cannot be normalized through the canonical mapping path MUST be
#: rejected with ``UNSUPPORTED_FEATURE``". Every other member is a step-(2) SELECTION
#: failure -- the route became a canonical declaration and then selected none of the
#: product's -- and on a package naming several routes that is a selected-set mismatch
#: rather than a per-route refusal. ``LEGACY_NOT_ACCEPTED`` is neither: it belongs to the
#: deprecated lane, which a product declaring ``format_options[]`` never reaches.
_RESOLUTION_FAILURES: frozenset[FormatRefusal] = frozenset(
    {FormatRefusal.OPTION_NOT_PUBLISHED, FormatRefusal.LEGACY_UNPROJECTABLE}
)


#: The declaration constraints that are a creative's SIZE. A manifest failing only these is
#: ``CREATIVE_SIZE_MISMATCH``: "The submitted creative dimensions do not match any size accepted
#: by the selected packages" (enums/error-code.json). Any other failure is not about size.
#: ``pixel_ratios`` is among them: a density the declaration does not accept is an intrinsic
#: size it does not accept -- "an accepted asset's intrinsic width is ``width x pixel_ratio``"
#: (formats/canonical/image.json). ``pixel_ratio`` is the same axis on a deprecated reference:
#: core/format-id.json's ``{width: 300, height: 250, pixel_ratio: 2}`` "identifies a 300x250
#: logical render supplied by a 600x500 image". ``aspect_ratio`` is the shape of the same
#: dimensions: an asset whose width and height disagree with the declared ratio matches no
#: size the declaration accepts (image.json: the ratio "narrows accepted entries").
_DIMENSION_CONSTRAINTS: frozenset[str] = frozenset(
    {
        "width",
        "height",
        "min_width",
        "max_width",
        "min_height",
        "max_height",
        "sizes",
        "pixel_ratios",
        "pixel_ratio",
        "aspect_ratio",
    }
)

#: The constraint a text value fails by lying outside a CLOSED SET the declaration publishes:
#: ``cta_values``, "Permitted CTA values for this product" (formats/canonical/image.json,
#: video_hosted.json), which on native_in_feed.json "narrows the cta slot to a closed enum".
#: Its own code is ``CREATIVE_VALUE_NOT_ALLOWED``: "A submitted text-asset value is not in the
#: format's declared ``allowed_values`` list ... a closed-set constraint violation that the
#: buyer can resolve mechanically" (enums/error-code.json), which the pinned native_in_feed
#: storyboard's ``reject_cta_not_in_enum`` step grades for exactly this case.
_CLOSED_VALUE_CONSTRAINT = "allowed_values"


@dataclass(frozen=True, slots=True)
class ManifestShortfall:
    """One way a creative's manifest fails the declaration it was graded against.

    Values, not a sentence: ``field`` is the manifest path (``assets.image_main.width``),
    ``constraint`` the declaration key that refused it (``width``, ``slots``),
    ``rejected_value`` what the asset states and ``accepted_values`` what the declaration
    accepts -- the pin's canonical rejection-set keys, which the raise site carries into
    ``problems[]`` unchanged. ``missing_destination`` marks a required click destination the
    manifest leaves out, which the pin gives its own code (:func:`creative_refusal`).
    """

    field: str
    constraint: str
    rejected_value: str | None
    accepted_values: tuple[str, ...]
    missing_destination: bool = False

    @property
    def dimensional(self) -> bool:
        """Whether this is a size failure (:data:`_DIMENSION_CONSTRAINTS`)."""
        return self.constraint in _DIMENSION_CONSTRAINTS

    @property
    def code(self) -> ErrorCode:
        """The pinned creative code this one failure is an instance of.

        ``CREATIVE_SIZE_MISMATCH`` for a size (:data:`_DIMENSION_CONSTRAINTS`),
        ``CREATIVE_MISSING_CLICK_URL`` for a required click destination left out
        (``missing_destination``), ``CREATIVE_VALUE_NOT_ALLOWED`` for a text value outside a closed set the declaration
        publishes (:data:`_CLOSED_VALUE_CONSTRAINT`), and ``CREATIVE_VALIDATION_FAILED_GENERIC``
        for anything else: "the producer cannot classify the failure with a more specific
        standard code" (enums/error-code.json).
        """
        if self.dimensional:
            return ErrorCode.CREATIVE_SIZE_MISMATCH
        if self.missing_destination:
            return ErrorCode.CREATIVE_MISSING_CLICK_URL
        if self.constraint == _CLOSED_VALUE_CONSTRAINT:
            return ErrorCode.CREATIVE_VALUE_NOT_ALLOWED
        return ErrorCode.CREATIVE_VALIDATION_FAILED_GENERIC


@dataclass(frozen=True, slots=True)
class FormatSatisfaction:
    """The verdict, carrying the VALUES a refusal needs and no authored sentence.

    ``accepted_values`` and ``rejected_value`` are the pin's canonical rejection-set keys
    (``core/error.json``), and they are what the SDK's own
    ``FormatKindNotInClosedSetError.to_wire_error`` puts in ``details`` -- so a caller can
    hand them straight to :class:`CapabilityRefusalDetails` without renaming anything.

    ``summary`` is for a LOG LINE and for the one caller that collects per-creative
    outcomes without raising (the lenient assignment mode). It is never buyer-facing: the
    buyer-facing sentence is a function of the code through ``CODE_TABLE``.

    ``selection`` is WHAT THE ROUTE SELECTED, and it is why this is a resolution and not a
    boolean. ``media-buy/package-request.json`` step (2) derives "the product
    ``format_options[]`` entries selected by each route" and requires "the selected option
    sets to match", so a caller has to be able to hold two resolutions side by side and
    compare the SETS they selected -- which a yes/no cannot express, and neither can one
    declaration per route: a broad legacy id selects every entry it is compatible with.
    The entries are kept in the product's declaration order, so two equal sets are two
    equal tuples. ``compare=False``: the selection is this route's OUTPUT, not part of the
    verdict's identity, and keeping it out of ``__eq__``/``__hash__`` means a frozen
    dataclass carrying unhashable pydantic models stays hashable.
    """

    satisfied: bool
    refusal: FormatRefusal | None = None
    rejected_value: str | None = None
    accepted_values: tuple[str, ...] = ()
    summary: str = ""
    # ``dataclass_field``, aliased at the import, because this class declares a member
    # literally named ``field`` below -- the plain ``field`` name would be shadowed by it.
    selection: tuple[LibraryFormatDeclaration, ...] = dataclass_field(default=(), compare=False)
    #: WHICH request field the refused route came from, when the caller supplied one on the
    #: route. ``core/error.json`` defines ``field`` as the pointer naming the rejected
    #: request field, and with several routes in play the right pointer is the ARM that
    #: failed -- a refusal naming ``format_kind`` for an unprojectable ``format_ids`` entry
    #: sends the buyer to edit a selector they got right. Carried here rather than mapped
    #: from ``refusal`` at the call site: the route knows its own provenance, and a mapping
    #: would be a second place that has to agree about which refusal came from which arm.
    field: str | None = None
    #: What a creative failed, one entry per constraint: its MANIFEST when the refusal is
    #: :attr:`FormatRefusal.MANIFEST_NOT_SATISFIED`, or the parameters of its deprecated
    #: reference when the refusal is :attr:`FormatRefusal.LEGACY_NOT_ACCEPTED` and the product
    #: takes the reference's template (:func:`_legacy_shortfalls`). ``compare=False`` for the
    #: reason ``selection`` gives: it is the verdict's evidence, not its identity.
    shortfalls: tuple[ManifestShortfall, ...] = dataclass_field(default=(), compare=False)

    @property
    def creative_coded(self) -> bool:
        """Whether a creative gate answers this refusal through :func:`creative_refusal`.

        Every refusal but one: a deprecated reference whose TEMPLATE the product does not take
        at all, which the creative assignment sites answer ``VALIDATION_ERROR`` themselves (and
        the lenient assignment mode skips). A deprecated reference the product does take, at
        other parameters, carries its shortfalls and gets the creative code the canonical lane
        gives the same condition.
        """
        return self.refusal is not FormatRefusal.LEGACY_NOT_ACCEPTED or bool(self.shortfalls)

    @property
    def declaration(self) -> LibraryFormatDeclaration | None:
        """The first entry the route selected: the one a package records once it is accepted."""
        return self.selection[0] if self.selection else None

    def __bool__(self) -> bool:
        return self.satisfied


_SATISFIED = FormatSatisfaction(satisfied=True)


@dataclass(frozen=True, slots=True)
class FormatRoute:
    """ONE way a buyer addressed a format, with the narrowing that route carries.

    ``media-buy/package-request.json`` gives a package three independent ways to name a
    format and permits several at once, and the pin is explicit that co-present routes are
    resolved SEPARATELY: step 13's "canonical precedence MUST NOT hide the dimensional
    conflict" only means anything if the legacy route is resolved even when a canonical one
    already succeeded. So a route is a value, and a claim naming three ways is three routes
    rather than one merged claim.

    ``params`` rides on the ROUTE and not beside it because it belongs to one route only --
    a buyer's ``params`` accompanies ``format_kind``, while an option ref and a legacy
    reference each name a declaration outright and carry no narrowing. Passing params
    alongside a set of routes would apply the kind route's dimensions to the legacy route's
    comparison. ``None`` means the route does not state its params at all; see
    :func:`satisfies`.
    """

    claim: FormatSelector
    params: Mapping[str, Any] | None = None
    #: The request field this route was read from, for the refusal's ``field`` pointer.
    #: ``None`` for a stored creative, whose identity is columns and not a request arm --
    #: those call sites name their own pointer (``packages``).
    field: str | None = None
    #: A canonical creative's ASSETS, keyed by slot id, as plain documents
    #: (:func:`manifest_document`). ``None`` for every route that is not a canonical
    #: creative -- a package selector carries no manifest, and a legacy creative's assets
    #: are keyed by its named format, not by a declaration's slots. When present, the
    #: declaration the route selects is graded against it (:func:`_manifest_shortfalls`).
    manifest: Mapping[str, Any] | None = None


def product_declarations(format_options: Any) -> tuple[LibraryFormatDeclaration, ...]:
    """A product's canonical declarations as the pinned union's arms, whatever shape arrives.

    THE reader of the canonical arm, beside ``format_resolver``'s three readers of the
    question's other halves. Those three take an ALREADY-CONVERTED product, whose
    ``format_options`` holds the union's arms; this one is also reached from an ORM row,
    where it holds documents. Both go through the same validation, which is what makes the
    stored shape and the served shape ONE answer rather than two -- measured: the first
    version of this validated into a different generated class whose ``params`` is an
    untyped dict, every converted declaration failed it, and every scenario in the
    satisfaction feature passed VACUOUSLY on the resulting empty closed set.

    A declaration the pinned union refuses is DROPPED with a warning rather than raising,
    the same choice ``format_identity_or_none`` makes about the legacy arm and for the same
    reason: one corrupt row must not turn a buyer's request into a 500. The consequence is
    stated rather than hidden -- a product whose declarations are all unparseable declares
    none, so it imposes no canonical restriction.
    """
    declarations = []
    for entry in format_options or []:
        try:
            declarations.append(_DECLARATION.validate_python(entry))
        except ValidationError:
            logger.warning("Unusable stored format_options declaration, ignored: %r", entry)
    return tuple(declarations)


def _plain(value: Any) -> Any:
    """A pinned params value as the plain scalar, list or mapping the rules compare.

    The generated per-kind params model is one level richer than a document: a nested
    object arrives as a MODEL and ``asset_source`` as an ``AssetSource`` enum member, and
    neither compares to the mapping or the string a buyer sends. Flattening here means the
    rules below are written once against plain values instead of each one re-deciding what
    it was handed.

    This is attribute READING, not serialization: ``Enum.value`` is the declared member and
    iterating a model yields its live field values. Nothing here coerces to JSON, which is
    what the serialization boundary bans and what would turn ``300`` into ``"300"`` halfway
    through a comparison.

    NO ROOTMODEL UNWRAPPING, and that is measured rather than assumed. The scalar wrappers
    the canonical schemas generate -- ``DurationMsRange`` and its siblings -- subclass
    ``adcp.types._scalar.ScalarInt`` and therefore ``int``: measured,
    ``isinstance(DurationMsRange(5000), int)`` is True, ``5000 <= DurationMsRange(5000)``
    is True, and ``float()`` of one is ``5000.0``. So the value READS directly and a
    ``.root`` hop would be the accident the no-defensive-RootModel guard names in its own
    refusal message. A branch doing that hop stood here and the guard caught it.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return {name: _plain(member) for name, member in value}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _declared_params(declaration: LibraryFormatDeclaration) -> Mapping[str, Any]:
    """The constraints one declaration states, as plain values keyed by param name."""
    params = _plain(declaration.params)
    return params if isinstance(params, Mapping) else {}


def _option_identity(declaration: LibraryFormatDeclaration) -> FormatOptionIdentity | None:
    """The option identity a declaration names, or None when it names no option.

    Built the way ``format_resolver.product_format_option_identities`` builds it, and for
    the reason stated there: a declaration carries no ``scope`` member, because the
    declaration schema's own allOf makes ``publisher_domain`` addressable only alongside
    ``format_option_id`` -- so the domain's PRESENCE is the discriminator, which is what
    :class:`FormatOptionIdentity` derives its scope from.
    """
    if not declaration.format_option_id:
        return None
    return FormatOptionIdentity(
        publisher_domain=declaration.publisher_domain,
        format_option_id=declaration.format_option_id,
    )


def _declaration_label(declaration: LibraryFormatDeclaration) -> str:
    """How one declaration is named back to the buyer in ``accepted_values``.

    The option id when the declaration publishes one, because that is the value a buyer
    puts in ``format_option_refs`` to select it; the kind otherwise, because a bare
    canonical declaration is selectable only by kind.
    """
    return declaration.format_option_id or str(declaration.format_kind)


def _is_number(value: Any) -> TypeGuard[int | float]:
    """Numeric for the params rules -- ``int``/``float`` but NOT ``bool``.

    ``bool`` is an ``int`` subclass in Python, so ``ssl_required: True`` would otherwise be
    graded as an exact dimension and a claim omitting it refused. The same exclusion the
    SDK's narrowing check makes, for the same reason.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric_range(value: Any) -> tuple[float, float] | None:
    """*value* as the ``[min, max]`` pair the canonical params use, or None.

    ``duration_ms_range: [5000, 60000]`` is the shape ``formats.mdx`` shows. An ENDPOINT
    MAY BE NULL AND NULL MEANS UNBOUNDED, which ``core/product-format-declaration.json``
    states normatively: "a null range endpoint is unbounded: ``[null, 60000]`` means up to
    60s, and ``[15000, null]`` means at least 15s; ``[null, null]`` is invalid because at
    least one endpoint must be bounded". Read as an infinity rather than special-cased, so
    the containment comparison below has one form -- treating a null as a missing range
    would make ``[null, 60000]`` impose nothing, which is the opposite of what it says.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    low, high = value
    if low is None and high is None:
        return None
    if not all(bound is None or _is_number(bound) for bound in (low, high)):
        return None
    return (float("-inf") if low is None else float(low), float("inf") if high is None else float(high))


def _slot_shortfall(claimed: Any, accepted: Any) -> list[str]:
    """Required slots of *accepted* that *claimed* does not cover, by group and asset type.

    ``formats.mdx``: "Required slots must be present with compatible asset types" -- so
    BOTH halves are matched, and a claim offering ``image_main`` as a ``text`` asset does
    not cover a required ``image_main`` image.

    A claim declaring NO slots is unconstrained here rather than refused, and that is a
    deliberate CEILING, not the rule: a package selector names a format, and the assets
    that fill its slots arrive later with the creative. Grading a selector as if it were a
    manifest would refuse every selector against every declaration that requires anything.
    The manifest-side grading -- required slots against a creative's actual assets -- is
    :func:`_manifest_slot_shortfalls`, which asks this function which required slots the
    manifest's assets leave uncovered.
    """
    if not isinstance(accepted, (list, tuple)) or not isinstance(claimed, (list, tuple)):
        return []
    offered = {(slot.get("asset_group_id"), slot.get("asset_type")) for slot in claimed if isinstance(slot, Mapping)}
    missing = []
    for slot in accepted:
        if not isinstance(slot, Mapping) or not slot.get("required"):
            continue
        if (slot.get("asset_group_id"), slot.get("asset_type")) not in offered:
            missing.append(str(slot.get("asset_group_id")))
    return missing


@lru_cache(maxsize=1)
def declared_slots_by_kind() -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    """Every canonical kind's declared slots, read off the PINNED UNION'S ARMS.

    ``core/product-format-declaration.json``'s ``params.slots[]`` carries
    ``{asset_group_id, asset_type, required}``, and each canonical format schema states its
    own list as the ``slots`` DEFAULT -- ``formats/canonical/image.json`` defaults to
    ``image_main`` (image) plus ``headline``/``body_text``/``primary_text``/``cta`` (text)
    and ``landing_page_url`` (url). So what a canonical creative's assets are CALLED is a
    property of the pin, and this is the one place that reads it.

    Read off the ARM'S FIELD DEFAULT and not off a validated instance, which was the first
    version of this and had a hole: two of the sixteen arms require a params member
    (``coordinated_placements.params.components``,
    ``seller_rendered_stateful_display.params.states``), so a declaration stating only its
    kind is REFUSED for them -- and ``seller_rendered_stateful_display`` declares four
    media slots (``state_canvases``, ``imagery``, ``logo``, ``video_main``) that the
    refusal would have silently dropped. A default is stated by the schema whether or not a
    sibling member is required, so reading it needs no instance to exist.

    DERIVED, so no spelling of a kind is hand-listed here; the population is asserted by
    ``tests/unit/test_canonical_declared_slots.py`` against ``CanonicalFormatKind``, which
    is what catches a codegen reshape that makes this walk return nothing (an empty map is
    indistinguishable from "no kind declares slots" at every call site).

    Cached because the answer is a function of the pin alone.
    """
    by_kind: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for arm in get_args(get_args(LibraryFormatDeclaration)[0]):
        kind = arm.model_fields["format_kind"].default
        params_model = arm.model_fields["params"].annotation
        slots = getattr(params_model, "model_fields", {}).get("slots")
        declared = slots.get_default() if slots is not None else None
        by_kind[str(kind)] = tuple(slot for slot in declared or () if isinstance(slot, Mapping))
    return by_kind


def canonical_slot_asset_ids(kind: str, asset_types: Collection[str]) -> frozenset[str]:
    """What canonical *kind* calls its assets of *asset_types*, per its declared slots.

    The answer the create-path asset gate needs: a creative identified by its canonical
    kind asserts no legacy reference, so no format spec resolves for it and there is no
    ``asset_id`` to ask a spec for. The declaration already says what the ids are.

    The KIND'S vocabulary, not one product's narrowing of it. A product MAY override the
    default -- the pin's own words are "make ``headline`` required, narrow ``cta`` to a
    value enum, or remove slots the surface doesn't consume" -- and all three of those
    narrow WITHIN this vocabulary. A product that invents a NEW media slot id is the one
    case this does not cover, and it is deliberately not covered here: reading ONE
    product's effective declaration is the manifest-against-declaration grading, which is
    a different question with a different input (the product, not the creative).

    Unknown kinds answer empty, which includes ``custom``: that arm's ``params`` is an open
    dict with no declared slots, because the pin routes a custom shape through a fetchable
    ``format_schema`` instead.
    """
    return _slot_asset_ids(declared_slots_by_kind().get(kind, ()), asset_types)


def declaration_slot_asset_ids(declaration: LibraryFormatDeclaration, asset_types: Collection[str]) -> frozenset[str]:
    """What ONE PRODUCT's declaration calls its assets of *asset_types*, as resolved.

    The question :func:`canonical_slot_asset_ids` deliberately does not answer. That one
    reads the KIND's vocabulary, which is a superset for the three overrides the pin names
    as examples ("make ``headline`` required, narrow ``cta`` to a value enum, or remove
    slots the surface doesn't consume") because all three narrow WITHIN it. It is NOT a
    superset for the fourth case the pin also shows: ``core/product-format-declaration.json``
    carries an ``audio_hosted`` example whose ``slots`` is ``[{asset_group_id: "script"},
    {asset_group_id: "offering_ref"}]``, REPLACING the kind's default list outright. A
    product that renames or adds a MEDIA slot that way names an asset id no kind declares,
    and a kind-only reader cannot find the creative's media at all.

    Reads the declaration's own ``params.slots``, which needs no new machinery: a VALIDATED
    declaration carries the arm's field default when it states no override, so a product
    that overrides nothing answers exactly what the kind answers (measured: a declaration
    stating only ``{format_kind: "image", params: {width, height}}`` yields image_main,
    headline, body_text, primary_text, cta, landing_page_url -- the arm's default list).
    That is what makes consulting this strictly safer than consulting the kind rather than
    a second, divergent answer.
    """
    slots = _declared_params(declaration).get("slots")
    return _slot_asset_ids(tuple(slot for slot in slots or () if isinstance(slot, Mapping)), asset_types)


def _slot_asset_ids(slots: tuple[Mapping[str, Any], ...], asset_types: Collection[str]) -> frozenset[str]:
    """The ``asset_group_id`` of each slot whose ``asset_type`` is in *asset_types*.

    One projection, shared by the kind-keyed and declaration-keyed readers above, so the
    two cannot come to disagree about what counts as a slot of a given type.
    """
    return frozenset(
        str(slot["asset_group_id"])
        for slot in slots
        if slot.get("asset_type") in asset_types and slot.get("asset_group_id")
    )


def _range_unsatisfied(claim: Any, low: float, high: float) -> bool:
    """Whether *claim* fails containment in ``[low, high]``.

    ``formats.mdx``: "A requested range must be contained within the accepted range;
    overlap is insufficient" -- so both ends of a claimed range are tested, and a claimed
    SCALAR is tested for membership. Silence is not a request and so cannot fail one.
    """
    if (requested := _numeric_range(claim)) is not None:
        return requested[0] < low or requested[1] > high
    return _is_number(claim) and not (low <= claim <= high)


#: The constraints a claim MUST STATE to satisfy, and the pin names them rather than
#: leaving them to be derived. ``docs/creative/canonical-formats.mdx`` § Format matching vs
#: product satisfaction: "When the product declares a fixed ``width``, ``height``,
#: ``duration_ms_exact``, or ``duration_ms_range``, the requested creative or package
#: selector MUST declare and match that constraint." Every OTHER constraint a claim is
#: silent about is inherited, not failed -- the same document's "Narrows" definition:
#: "when v2.params omits a parameter that v1 specified, v2 inherits v1's value (no
#: narrowing constraint added)". The pin's own canonical_formats storyboard depends on it:
#: ``create_media_buy_with_equivalent_direct_legacy_emission`` sends a direct route of
#: ``{width, height}`` against a declaration that also fixes ``max_file_size_kb`` and
#: ``image_formats``, and grades it ACCEPTED with every route evaluated independently.
_MUST_BE_STATED: frozenset[str] = frozenset({"width", "height", "duration_ms_exact", "duration_ms_range"})

#: What a creative MANIFEST must state, which is more than a selector must. A selector names
#: a format and inherits every cap it is silent about; a manifest is the creative itself, and
#: a cap it does not answer cannot be checked, so accepting it would "silently relax the
#: target contract" (creative-manifests.mdx § Validation step 5; step 4 lists "file-size
#: limits" among what is checked). ``core/assets/image-asset.json`` states it for the image
#: cap outright -- ``file_size_bytes`` is "Required by agents that advertise a
#: max_file_size_kb constraint" -- and the video and audio cap is the same check in megabytes.
_MANIFEST_MUST_BE_STATED: frozenset[str] = _MUST_BE_STATED | {"max_file_size_kb", "max_file_size_mb"}


def _constraint_unsatisfied(key: str, bound: Any, claim: Any, must_state: frozenset[str] = _MUST_BE_STATED) -> bool:
    """Whether *claim* fails the single declaration constraint ``key: bound``.

    SILENCE is decided first and by name, because the pin decides it by name: a claim
    silent about a constraint in :data:`_MUST_BE_STATED` fails it -- the whole of
    ``reject_bare_image_selector_for_fixed_mrec``, where a bare ``image`` selector states
    no width against a product fixing ``width: 300`` -- and a claim silent about any other
    constraint inherits it. A ``None`` bound is no constraint at all: the validated
    declaration carries every param its kind declares, set or not.

    A STATED claim is graded by the shape of the constraint, never by a list of field
    names, because a params vocabulary is sixteen per-kind schemas wide and grows with the
    spec:

    * ``max_*`` / ``min_*`` are BOUNDS, and a claim satisfies one by being inside it.
    * ``*_range`` over two numbers is a RANGE, graded by :func:`_range_unsatisfied`. By the
      NAME as well as the shape: a set of two numbers is still a set -- ``pixel_ratios:
      [1, 2]`` "accepts both standard and Retina renditions" (formats/canonical/image.json),
      not every density between them.
    * any other NUMBER must be EQUAL -- ``formats.mdx``: "Exact dimensions and durations
      must match".
    * any LIST is an allowed SET, and a claim's values must be a subset.
    * anything else (a string, a bool) must be EQUAL.

    ``slots`` is not graded here: it is the one constraint whose failure names WHICH slot,
    so it has its own reader (:func:`_slot_shortfall`) and its own caller.

    *must_state* is the silence list: :data:`_MUST_BE_STATED` for a selector, the wider
    :data:`_MANIFEST_MUST_BE_STATED` for a creative manifest.
    """
    if bound is None:
        return False
    if claim is None:
        return key in must_state
    if _is_number(bound) and key.startswith("max_"):
        return _is_number(claim) and claim > bound
    if _is_number(bound) and key.startswith("min_"):
        return _is_number(claim) and claim < bound
    if key.endswith("_range") and (window := _numeric_range(bound)) is not None:
        return _range_unsatisfied(claim, *window)
    if _is_number(bound):
        return not _is_number(claim) or claim != bound
    if isinstance(bound, (list, tuple, set)):
        values = claim if isinstance(claim, (list, tuple, set)) else (claim,)
        return any(value not in bound for value in values)
    return claim != bound


def _unsatisfied_params(
    claimed: Mapping[str, Any], accepted: Mapping[str, Any], must_state: frozenset[str] = _MUST_BE_STATED
) -> list[str]:
    """The declaration's params *claimed* fails, named by key. Empty means it satisfies.

    Directional, as both authorities say compatibility is: only the TARGET's constraints
    are graded, so a claim carrying a param the declaration does not mention is ignored
    rather than refused -- which is what lets a buyer send ``max_file_size_kb`` to a
    declaration that constrains only dimensions.

    ONE PRECEDENCE RULE is stated by the pin and applied here before the per-constraint
    rules: "Duration precedence is ``duration_ms_exact`` > ``duration_ms_range``"
    (``core/product-format-declaration.json``). A declaration carrying both states the
    exact value as its constraint, and grading the range alongside it would let a claim
    inside the range but unequal to the exact value fail twice, or -- worse, once the
    exact value is the constraint -- report the range as the thing to fix.

    AND ONE CROSS-KEY RULE: a declared ``duration_ms_range`` is met by a claimed
    ``duration_ms_exact`` that falls inside it -- ``canonical-formats.mdx``: "An exact
    value, such as ``duration_ms_exact``, satisfies a range when that exact value falls
    inside the accepted interval." Without it a claim stating an exact duration would be
    read as silent about the range it states a value inside.
    """
    if "duration_ms_exact" in accepted and accepted.get("duration_ms_exact") is not None:
        accepted = {k: v for k, v in accepted.items() if k != "duration_ms_range"}
    failures: list[str] = []
    for key, bound in accepted.items():
        claim = claimed.get(key)
        if key == "duration_ms_range" and claim is None:
            claim = claimed.get("duration_ms_exact")
        if key == "slots":
            failures.extend(f"slots.{group}" for group in _slot_shortfall(claim, bound))
        elif _constraint_unsatisfied(key, bound, claim, must_state):
            failures.append(key)
    return failures


def _stated_constraints(accepted: Mapping[str, Any], claimed: Mapping[str, Any]) -> dict[str, Any]:
    """The constraints in *accepted* that *claimed* speaks to. The claim inherits the rest.

    For a claim that already NAMES its declaration -- an option ref, or a legacy reference
    whose projection is compared under "v2 narrows v1" -- only what the claim states is
    graded. ``canonical-formats.mdx`` § "Narrows" -- formal definition (normative): "when
    v1 says nothing about a parameter but v2 specifies one ... v2 is ``narrowing``", so a
    constraint the claim leaves unstated is no conflict, unlike a bare kind, which proves
    nothing and owes every constraint (:func:`_graded`).

    A stated ``duration_ms_exact`` also speaks to a declared ``duration_ms_range``: the
    cross-key rule in :func:`_unsatisfied_params`, "an exact value ... satisfies a range
    when that exact value falls inside the accepted interval". Without it the range would
    be dropped as unstated and never graded.
    """
    stated = set(claimed)
    if "duration_ms_exact" in claimed:
        stated.add("duration_ms_range")
    return {key: bound for key, bound in accepted.items() if key in stated}


#: What one media asset can STATE, as ``(declaration constraint, asset member)`` pairs per
#: ``asset_type``. The asset members are the pinned asset schemas' own
#: (``core/assets/image-asset.json``, ``video-asset.json``, ``audio-asset.json``); the
#: constraint names are the canonical declaration params they answer. A dimension answers
#: its exact key and both of its bounds, because a declaration may state either.
#:
#: A key absent here is a constraint no asset of that type can speak to, and it is not
#: graded on a manifest at all: a manifest cannot be held to a fact it has no member for.
#: A key present here and missing from the asset is graded as SILENCE, which fails only
#: for the constraints a manifest must state (:data:`_MANIFEST_MUST_BE_STATED`: the fixed
#: dimensions and durations, and a file-size cap) and is inherited for every other.
_ASSET_FACTS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "image": (
        ("width", "width"),
        ("min_width", "width"),
        ("max_width", "width"),
        ("height", "height"),
        ("min_height", "height"),
        ("max_height", "height"),
        ("max_file_size_kb", "file_size_bytes"),
        ("image_formats", "format"),
        ("pixel_ratios", "pixel_ratio"),
    ),
    "video": (
        ("width", "width"),
        ("min_width", "width"),
        ("max_width", "width"),
        ("height", "height"),
        ("min_height", "height"),
        ("max_height", "height"),
        ("duration_ms_exact", "duration_ms"),
        ("max_file_size_mb", "file_size_bytes"),
        ("video_codecs", "video_codec"),
        ("audio_codecs", "audio_codec"),
        ("containers", "container_format"),
        ("min_bitrate_kbps", "video_bitrate_kbps"),
        ("max_bitrate_kbps", "video_bitrate_kbps"),
        ("frame_rates", "frame_rate"),
    ),
    "audio": (
        ("duration_ms_exact", "duration_ms"),
        ("max_file_size_mb", "file_size_bytes"),
        ("audio_codecs", "codec"),
        ("min_bitrate_kbps", "bitrate_kbps"),
        ("max_bitrate_kbps", "bitrate_kbps"),
        ("audio_sample_rates", "sampling_rate_hz"),
        ("audio_channels", "channels"),
    ),
}

#: The asset members a pinned asset schema types as a STRING holding a number, read as the
#: number the declaration states: video-asset.json ``frame_rate`` is "Frame rate as string to
#: preserve precision (e.g., '23.976', '29.97', '30')", and video_hosted.json ``frame_rates``
#: is an array of numbers.
_NUMERIC_STRING_MEMBERS: frozenset[str] = frozenset({"frame_rate"})

#: Bytes per unit of a file-size cap, for an asset that states ``file_size_bytes``. Both are
#: DECIMAL. ``docs/creative/canonical-formats.mdx``: "a field ending in ``_kb`` uses 1 KB =
#: 1,000 bytes, a field ending in ``_mb`` uses 1 MB = 1,000,000 bytes ... Implementations MUST
#: NOT substitute binary KiB/MiB units for these fields"; ``core/product-format-declaration.json``
#: says the same of ``_kb``, and ``video_hosted.json`` / ``audio_hosted.json`` of ``_mb``.
_BYTES_PER_UNIT: Mapping[str, int] = {"max_file_size_kb": 1000, "max_file_size_mb": 1_000_000}


def manifest_document(assets: Any) -> dict[str, Any]:
    """A creative's ``assets`` as plain documents keyed by slot id, whatever shape arrives.

    Two shapes reach here: the stored document (dicts, after the JSON column) and, inside
    the transaction that just wrote the row, the pinned asset MODELS the sync handed the
    column. Both read the same once flattened by :func:`_plain`. Three asset types are
    root models over their payload (``vast``, ``daast``, ``display_tag``), so a root model
    is read through its root.
    """
    return (
        {str(group): _asset_document(value) for group, value in assets.items()} if isinstance(assets, Mapping) else {}
    )


def _asset_document(value: Any) -> Any:
    """One asset value as plain data (see :func:`manifest_document`)."""
    if isinstance(value, RootModel):
        return _asset_document(value.root)
    if isinstance(value, (list, tuple)):
        return [_asset_document(item) for item in value]
    if isinstance(value, Mapping):
        return dict(value)
    return _plain(value)


def _asset_types(value: Any) -> set[str]:
    """The ``asset_type`` of each asset a slot holds -- one, or each item of a multi-asset slot."""
    items = value if isinstance(value, list) else [value]
    return {str(item.get("asset_type")) for item in items if isinstance(item, Mapping)}


def _shown(value: Any) -> str:
    """A constraint or asset value as the string a ``problems[]`` entry carries.

    A whole number is rendered as a plain ``int``: the generated duration scalars subclass
    ``int`` (see :func:`_plain`) and their own ``str`` is their repr, ``DurationMsRange(5000)``.
    """
    plain = _plain(value)
    if _is_number(plain) and float(plain).is_integer():
        return str(int(plain))
    return str(plain)


def _shown_all(bound: Any) -> tuple[str, ...]:
    """A declared bound as ``accepted_values``: each member of a set or range, else the value."""
    return tuple(_shown(item) for item in bound) if isinstance(bound, (list, tuple)) else (_shown(bound),)


def _manifest_slot_shortfalls(
    slots: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]
) -> list[ManifestShortfall]:
    """Steps 2 and 3 of creative-manifests.mdx § Validation, over the declaration's slots.

    "Confirm all required slots are present" is :func:`_slot_shortfall`'s question, asked of
    the slots the manifest's assets fill, so a required slot filled with the wrong asset type
    is missing exactly as it is for a selector. "Validate each asset against its declared
    type" also covers an OPTIONAL slot the manifest fills: a declared slot holding an asset of
    another type is refused whether or not the slot was required. An asset keyed by no
    declared slot is not graded: the declaration says nothing about it.

    A missing slot is marked ``missing_destination`` when it is the click destination, by
    the declaration's own signal (:func:`declared_slot_mechanism`): the url slot's ``role``,
    else its ``asset_group_id`` in the vocabulary registry -- ``landing_page_url`` on a
    canonical declaration, its alias ``click_url`` on a named format.
    """
    from src.core.helpers.creative_helpers import declared_slot_mechanism  # the package init imports adapters

    offered = [
        {"asset_group_id": group, "asset_type": asset_type}
        for group, value in manifest.items()
        for asset_type in _asset_types(value)
    ]
    shortfalls = [
        ManifestShortfall(
            field=f"assets.{group}",
            constraint="slots",
            rejected_value=None,
            accepted_values=tuple(str(slot.get("asset_type")) for slot in slots if slot.get("asset_group_id") == group),
            missing_destination=any(
                slot.get("asset_group_id") == group
                and slot.get("asset_type") == "url"
                and declared_slot_mechanism(group, slot.get("requirements")) == "clickthrough"
                for slot in slots
            ),
        )
        for group in _slot_shortfall(offered, slots)
        if group not in manifest
    ]
    for slot in slots:
        group, expected = str(slot.get("asset_group_id") or ""), slot.get("asset_type")
        if group in manifest and expected and (held := _asset_types(manifest[group])) != {str(expected)}:
            shortfalls.append(
                ManifestShortfall(
                    field=f"assets.{group}.asset_type",
                    constraint="slots",
                    rejected_value=",".join(sorted(held)) or None,
                    accepted_values=(str(expected),),
                )
            )
    return shortfalls


#: The asset members a declaration states in LOGICAL pixels. An image asset carries its
#: INTRINSIC width and height, and the declaration's ``width``/``height``/``sizes``/bounds are
#: logical render sizes: ``formats/canonical/image.json`` -- "an accepted asset's intrinsic
#: width is ``width x pixel_ratio``" -- so the asset's members are divided by its ratio before
#: they are compared.
_INTRINSIC_MEMBERS: frozenset[str] = frozenset({"width", "height"})


def _asset_claim(asset: Mapping[str, Any], ratio: float = 1) -> tuple[dict[str, Any], dict[str, str]]:
    """What one media asset states, in the declaration's vocabulary, and from which member.

    The claim is what :func:`_unsatisfied_params` grades, so a manifest is held to a
    declaration by the same matcher as a selector. Every key the asset's type CAN state is
    in the claim, ``None`` where the asset is silent, which is what makes silence about a
    fixed width a failure.

    *ratio* is the asset's effective pixel ratio (:func:`_candidate_ratios`). The dimensions
    are claimed in logical pixels -- intrinsic over the ratio -- and the ratio itself is the
    claim against ``pixel_ratios``, so a 600x500 image at 2x claims 300x250 at density 2.

    ``sizes`` is one more key, built from both dimensions: an image or video declaration may
    state its accepted sizes as a list of ``{width, height}`` rather than one fixed pair.
    """
    facts = _ASSET_FACTS[str(asset.get("asset_type"))]
    members = dict(facts)
    claim: dict[str, Any] = {}
    for constraint, member in facts:
        value = _plain(asset.get(member))
        if member in _NUMERIC_STRING_MEMBERS and isinstance(value, str):
            value = _number_or_text(value)
        divisor = _BYTES_PER_UNIT.get(constraint) or (ratio if member in _INTRINSIC_MEMBERS else 1)
        claim[constraint] = value / divisor if divisor != 1 and _is_number(value) else value
    if "pixel_ratios" in members:
        claim["pixel_ratios"] = ratio
    if "width" in members:
        width, height = claim["width"], claim["height"]
        claim["sizes"] = None if width is None or height is None else {"width": width, "height": height}
        members["sizes"] = "width"
    if "duration_ms_exact" in members:
        members["duration_ms_range"] = members["duration_ms_exact"]
    return claim, members


def _number_or_text(value: str) -> float | str:
    """*value* as a number when it reads as one, else unchanged, so it fails a numeric set."""
    try:
        return float(value)
    except ValueError:
        return value


def _candidate_ratios(asset: Mapping[str, Any], declared: Mapping[str, Any]) -> tuple[float, ...]:
    """The pixel ratios *asset* may be graded at against *declared*.

    ``formats/canonical/image.json`` ``pixel_ratios``: "SDKs determine the effective ratio from
    ``asset.pixel_ratio`` when supplied, otherwise infer it from intrinsic asset dimensions
    divided by the matched logical size." So a stated ratio is the only candidate, and an
    unstated one is each ratio the declaration accepts: the asset satisfies when one of them
    turns its intrinsic size into an accepted logical size, which is the inference for a
    fixed size, for each entry of ``sizes[]`` and for a bounded range alike. Width and height
    are divided by the same ratio, so "Width and height ratios MUST agree" is built in.

    One candidate, ratio 1, where the ratio does not apply: an asset type with no
    ``pixel_ratio`` member, or a declaration whose kind has no ``pixel_ratios`` parameter.
    """
    if "pixel_ratios" not in dict(_ASSET_FACTS[str(asset.get("asset_type"))]) or "pixel_ratios" not in declared:
        return (1,)
    stated = _plain(asset.get("pixel_ratio"))
    return (stated,) if _is_number(stated) else (tuple(declared["pixel_ratios"]) or (1,))


def _primary_media_slot(slots: Sequence[Mapping[str, Any]]) -> str | None:
    """The slot whose asset the declaration's params describe: its first REQUIRED media slot.

    A canonical declaration states its dimensions and duration once, for the creative's main
    media -- ``image_main`` for ``image``, ``video_main`` for ``video_hosted``, ``audio_main``
    for ``audio_hosted`` -- and that slot is the one each kind's schema requires. A product
    that replaces the slot list names its own required media slot the same way. A
    declaration with no required media slot (a tag, a bundle, a catalog) has no asset whose
    members state those params, so none is graded.
    """
    return next(
        (
            str(slot["asset_group_id"])
            for slot in slots
            if slot.get("required") and slot.get("asset_group_id") and slot.get("asset_type") in _ASSET_FACTS
        ),
        None,
    )


def _manifest_shortfalls(declaration: LibraryFormatDeclaration, manifest: Mapping[str, Any]) -> list[ManifestShortfall]:
    """Everything *manifest* fails in *declaration*: steps 2 to 4 of § Validation.

    The slots first (:func:`_manifest_slot_shortfalls`), then the main media asset's facts
    against the declaration's params (:func:`_asset_claim` graded by
    :func:`_unsatisfied_params`). Only the constraints the asset type can state are graded
    (:func:`_stated_constraints` over the claim's keys); among those, silence fails where
    the pin requires a creative to declare the constraint (:data:`_MANIFEST_MUST_BE_STATED`).
    A main slot the manifest leaves empty is reported once, as the missing slot, and its
    facts are not graded.

    The accepted densities are the slot's (:func:`_accepted_ratios`), and each asset is graded
    at each candidate ratio (:func:`_candidate_ratios`) with the fewest shortfalls reported,
    which is none when any candidate satisfies.

    A main slot holding an ARRAY is a rendition set, and every rendition is graded on its own,
    its path indexed (``assets.image_main[1].width``) so two renditions failing the same member
    are two problems. ``formats/canonical/_base.json`` ``required_pixel_ratios``: "the manifest
    slot value MUST be an array containing exactly one matching image rendition for each
    required ratio" -- graded on the ratio each rendition was graded at
    (:func:`_coverage_shortfalls`).
    """
    declared = dict(_declared_params(declaration))
    slots = tuple(_effective_slot(slot, declared) for slot in declared.get("slots") or () if isinstance(slot, Mapping))
    shortfalls = _manifest_slot_shortfalls(slots, manifest)
    primary = _primary_media_slot(slots)
    shortfalls.extend(_slot_constraint_shortfalls(slots, manifest, primary))
    value = manifest.get(primary) if primary is not None else None
    renditions = [
        (f"{primary}[{index}]" if isinstance(value, list) else str(primary), item)
        for index, item in enumerate(value if isinstance(value, list) else [value])
        if isinstance(item, Mapping) and str(item.get("asset_type")) in _ASSET_FACTS
    ]
    if not renditions:
        return shortfalls
    slot = next(slot for slot in slots if slot.get("asset_group_id") == primary)
    if "pixel_ratios" in declared:
        declared["pixel_ratios"] = _accepted_ratios(declared["pixel_ratios"], slot.get("pixel_ratios"))
    graded = [(path, *_graded_rendition(path, asset, declared)) for path, asset in renditions]
    shortfalls.extend(shortfall for _path, _ratio, failed in graded for shortfall in failed)
    shortfalls.extend(_coverage_shortfalls(str(primary), slot.get("required_pixel_ratios"), graded))
    ratio = declared.get("aspect_ratio") or _ORIENTATION_RATIOS.get(str(declared.get("orientation")))
    shortfalls.extend(shortfall for path, asset in renditions for shortfall in _aspect_shortfalls(path, asset, ratio))
    shortfalls.extend(shortfall for path, asset in renditions for shortfall in _level_shortfalls(path, asset, declared))
    return shortfalls


def _level_shortfalls(path: str, asset: Mapping[str, Any], declared: Mapping[str, Any]) -> list[ManifestShortfall]:
    """The main media asset against the declared constraints that are neither a set nor a bound.

    Three, each read by its own rule because its name does not say its shape:

    * ``loudness_lufs`` with ``loudness_tolerance_db`` -- formats/canonical/audio_hosted.json,
      "Required integrated loudness in LUFS" and "Permitted deviation from loudness_lufs in
      dB". The asset's ``loudness_lufs`` (core/assets/audio-asset.json, "Integrated loudness
      in LUFS") must lie within the tolerance of the target. A declaration stating no
      tolerance permits no deviation: the pin gives no default, and reading a missing
      tolerance as zero is this seller's choice, the one that does not relax the target.
    * ``true_peak_dbfs`` -- audio_hosted.json, "Maximum true-peak level in dBFS"; the asset's
      ``true_peak_dbfs`` may not exceed it.
    * ``captions: required`` -- formats/canonical/video_hosted.json ``captions`` (required,
      recommended, not_required); a required caption track is the asset's ``captions_url``
      (core/assets/video-asset.json), and a video without one is missing it.

    A level the asset does not state is not graded: audio-asset.json makes both members
    optional, and only the constraints in :data:`_MANIFEST_MUST_BE_STATED` fail on silence.
    """
    asset_type = str(asset.get("asset_type"))
    shortfalls: list[ManifestShortfall] = []
    target, stated = declared.get("loudness_lufs"), _plain(asset.get("loudness_lufs"))
    if asset_type == "audio" and _is_number(target) and _is_number(stated):
        tolerance = declared.get("loudness_tolerance_db")
        tolerance = tolerance if _is_number(tolerance) else 0
        if abs(stated - target) > tolerance:
            window = (target,) if not tolerance else (target - tolerance, target + tolerance)
            shortfalls.append(
                ManifestShortfall(f"assets.{path}.loudness_lufs", "loudness_lufs", _shown(stated), _shown_all(window))
            )
    ceiling, peak = declared.get("true_peak_dbfs"), _plain(asset.get("true_peak_dbfs"))
    if asset_type == "audio" and _is_number(ceiling) and _is_number(peak) and peak > ceiling:
        shortfalls.append(
            ManifestShortfall(f"assets.{path}.true_peak_dbfs", "true_peak_dbfs", _shown(peak), (_shown(ceiling),))
        )
    if asset_type == "video" and declared.get("captions") == "required" and not asset.get("captions_url"):
        shortfalls.append(ManifestShortfall(f"assets.{path}.captions_url", "captions", None, ("required",)))
    return shortfalls


#: The aspect ratio each video ``orientation`` names. ``formats/canonical/video_hosted.json``
#: (and ``video_vast.json``): "Vertical = 9:16 (Reels, Stories, Shorts). Horizontal = 16:9
#: (instream, CTV). Square = 1:1 (in-feed)", and its ``aspect_ratio``: "Inferred from
#: orientation if omitted".
_ORIENTATION_RATIOS: Mapping[str, str] = {"vertical": "9:16", "horizontal": "16:9", "square": "1:1"}


def _ratio_agrees(width: Any, height: Any, ratio: str) -> bool:
    """Whether a *width* x *height* asset has the ``"W:H"`` aspect *ratio* a declaration states.

    ``formats/canonical/image.json`` ``aspect_ratio`` ("e.g., '1.91:1', '1:1'") and
    ``video_hosted.json``'s state the ratio and no tolerance, so the comparison is made AT THE
    RATIO'S OWN PRECISION: exactly for whole terms (``9:16`` takes 1080x1920 and nothing
    else), and to the decimals the ratio is written in otherwise (``1.91:1`` takes 1200x628,
    whose 1.9108 is 1.91 to two places). That precision is this seller's reading of a ratio
    the pin writes to two decimals and never as an exact fraction.
    """
    numerator, _, denominator = ratio.partition(":")
    try:
        terms = (Decimal(numerator), Decimal(denominator))
    except InvalidOperation:
        return True
    if not (_is_number(width) and _is_number(height)) or not all(terms) or not height:
        return True
    if all(term == term.to_integral_value() for term in terms):
        return Decimal(width) * terms[1] == Decimal(height) * terms[0]
    places = max(-min(int(term.as_tuple().exponent), 0) for term in terms)
    return round(Decimal(width) / Decimal(height), places) == round(terms[0] / terms[1], places)


def _aspect_shortfalls(path: str, asset: Mapping[str, Any], ratio: Any) -> list[ManifestShortfall]:
    """The image or video asset in *path* against the aspect ratio its declaration accepts.

    *ratio* is one declared ``"W:H"`` ratio -- the declared ``aspect_ratio``, or the one a
    video ``orientation`` names when the declaration omits it (:data:`_ORIENTATION_RATIOS`) --
    or a SET of them, responsive_creative.json ``logo_aspect_ratios``, which an asset agrees
    with by agreeing with any one. No ratio stated is no constraint.

    Step 4 of creative-manifests.mdx § Validation checks "fixed dimensions", and image.json
    says a declared ratio narrows every size mode: "When provided alongside width/height, must
    agree. When used with sizes[] or responsive ranges, narrows accepted entries to those
    matching the aspect ratio." An asset that states no width or height is not graded here;
    a fixed size it is silent about already fails.
    """
    ratios = (ratio,) if isinstance(ratio, str) else tuple(map(str, ratio)) if isinstance(ratio, (list, tuple)) else ()
    width, height = asset.get("width"), asset.get("height")
    if not ratios or any(_ratio_agrees(width, height, accepted) for accepted in ratios):
        return []
    return [ManifestShortfall(f"assets.{path}", "aspect_ratio", f"{_shown(width)}:{_shown(height)}", ratios)]


def _set_shortfalls(path: str, member: str, constraint: str, stated: Any, accepted: Any) -> list[ManifestShortfall]:
    """*stated*, the asset's ``member``, against the closed set *accepted* a declaration publishes.

    The one membership check every such set goes through: a slot's accepted ``widths`` and
    ``heights``, its ``pixel_ratios``, ``cta_values`` (:data:`_CLOSED_VALUE_CONSTRAINT`) and a
    card's ``allowed_card_media_asset_types``. The failure is reported at
    ``assets.<path>.<member>`` under *constraint*. A value the asset does not state, and a set
    the declaration does not publish, are not graded here.
    """
    if stated is None or not isinstance(accepted, (list, tuple)) or stated in accepted:
        return []
    return [ManifestShortfall(f"assets.{path}.{member}", constraint, _shown(stated), _shown_all(accepted))]


#: The per-slot constraints a declaration may state FLAT, as ``<role>_<suffix>``, and the
#: slot-entry constraint each suffix is. ``formats/canonical/_base.json`` ``slots``:
#: "Format-level narrowing parameters that apply across all slots (e.g., flat
#: ``headline_max_chars`` on responsive_creative) may also live on the format declaration",
#: and the canonical schemas spell them so: responsive_creative.json ``headline_max_chars`` /
#: ``headlines_min`` / ``images_landscape_aspect_ratio`` ("per-slot count/length narrowing lives
#: in flat parameters"), audio_hosted.json ``companion_image_max_file_size_kb`` /
#: ``companion_image_aspect_ratio`` / ``companion_image_required``, video_hosted.json
#: ``companion_banner_widths`` / ``companion_banner_heights``, responsive_creative.json
#: ``video_min_duration_ms`` / ``video_max_duration_ms`` / ``logo_aspect_ratios``, and the
#: ``cta_values`` image.json, native_in_feed.json and video_hosted.json state for the ``cta``
#: slot ("When set, narrows the cta slot to a closed enum"). ``max_file_size_kb`` is the
#: slot's ``max_size_kb`` in the same unit: canonical-formats.mdx, "a field ending in ``_kb``
#: uses 1 KB = 1,000 bytes". The suffixes are tried longest first, so ``_max_chars`` is never
#: read as ``_max`` and ``_aspect_ratios`` never as ``_aspect_ratio``.
_FLAT_SLOT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_max_file_size_kb", "max_size_kb"),
    ("_min_duration_ms", "min_duration_ms"),
    ("_max_duration_ms", "max_duration_ms"),
    ("_aspect_ratios", "aspect_ratios"),
    ("_aspect_ratio", "aspect_ratio"),
    ("_max_chars", "max_chars"),
    ("_required", "required"),
    ("_heights", "heights"),
    ("_widths", "widths"),
    ("_values", _CLOSED_VALUE_CONSTRAINT),
    ("_min", "min"),
    ("_max", "max"),
)

#: The same count limits spelled as a PREFIX: image_carousel.json ``min_cards`` ("Minimum card
#: count") and ``max_cards`` ("Maximum card count"), which constrain the ``cards`` slot as its
#: own ``min`` / ``max`` do ("``min`` / ``max`` constrain card count"). Tried only when no
#: suffix matched, and read only when the rest of the name is the slot's role, so
#: ``max_width`` or ``max_file_size_kb`` name no slot and stay the main media's params.
_FLAT_SLOT_PREFIXES: tuple[tuple[str, str], ...] = (("min_", "min"), ("max_", "max"))

#: A per-asset-type file-size cap spelled with the bound FIRST, ``max_<type>_file_size_kb``:
#: native_in_feed.json ``max_image_file_size_kb``, "Maximum file size in kilobytes for
#: main_image and icon" -- the two image slots that format declares. So it names the same
#: role responsive_creative.json's ``asset_image_max_file_size_kb`` does, every slot holding
#: an image (``asset_<type>``, :func:`_effective_slot`), and is that slot's ``max_size_kb``
#: in decimal kilobytes (canonical-formats.mdx: "1 KB = 1,000 bytes"). Tried before the
#: prefixes, so ``max_`` never reads it as a count; the main media's own ``max_file_size_kb``
#: names no type and is left to them.
_TYPED_FILE_SIZE_SUFFIX = "_file_size_kb"

#: The two asset types a card's ``media`` may carry (core/assets/card-asset.json: "Either an
#: ``image`` or ``video`` asset"), which is also how image_carousel.json names the per-type
#: limits on it: ``card_image_max_file_size_kb``, ``card_video_max_file_size_kb``,
#: ``card_video_max_duration_ms``.
_CARD_MEDIA_TYPES: frozenset[str] = frozenset({"image", "video"})

#: image_carousel.json ``allowed_card_media_asset_types``: "Asset types each card's ``media``
#: field may carry. Default: ['image']." A declaration silent about it takes the image-only
#: default the parameter states, the reading that does not relax the target contract. The
#: format's own description ("Allowed asset types for a card's ``media`` field: ``image`` and
#: ``video``") reads the other way; adcontextprotocol/adcp#8629 asks which is intended.
_DEFAULT_CARD_MEDIA_TYPES: tuple[str, ...] = ("image",)


def _flat_constraint(key: str) -> tuple[str, str]:
    """The slot constraint flat declaration param *key* states, and the role it names.

    ``("", "")`` when the name has neither a suffix (:data:`_FLAT_SLOT_SUFFIXES`), nor the
    typed file-size spelling (:data:`_TYPED_FILE_SIZE_SUFFIX`), nor a prefix
    (:data:`_FLAT_SLOT_PREFIXES`) this module reads.
    """
    for suffix, constraint in _FLAT_SLOT_SUFFIXES:
        if key.endswith(suffix):
            return constraint, key[: -len(suffix)]
    if key.startswith("max_") and key.endswith(_TYPED_FILE_SIZE_SUFFIX):
        if asset_type := key[len("max_") : -len(_TYPED_FILE_SIZE_SUFFIX)]:
            return "max_size_kb", f"asset_{asset_type}"
    for prefix, constraint in _FLAT_SLOT_PREFIXES:
        if key.startswith(prefix):
            return constraint, key[len(prefix) :]
    return "", ""


def _slot_roles(group: str) -> frozenset[str]:
    """The role names a flat param may give slot *group*: its id, singular or plural.

    The pin names a pool slot in the plural and its flat limits in either number:
    responsive_creative.json's ``headlines`` slot takes ``headlines_min`` and
    ``headline_max_chars``, and its ``video`` slot ``videos_min``.
    """
    return frozenset({group, group.removesuffix("s"), f"{group}s"})


def _effective_slot(slot: Mapping[str, Any], declared: Mapping[str, Any]) -> dict[str, Any]:
    """*slot* with the flat declaration params that name its role folded in (:data:`_FLAT_SLOT_SUFFIXES`).

    A param is read as narrowing the slot, never as relaxing it: of a count or cap stated on
    both, the stricter holds; a flat ``required`` can require an optional slot and cannot
    release a required one; an aspect ratio or a width or height set stated on the slot entry
    wins over the flat one.

    ``<singular>_<member>_max_chars`` limits a STRING MEMBER of each asset the slot holds,
    which is how image_carousel.json spells the card's text: ``card_headline_max_chars``
    "Governs the ``headline`` field on each card-asset in the ``cards`` slot", and
    ``card_description_max_chars`` its ``description``. Those land in ``member_max_chars``.

    ``asset_<type>_<suffix>`` names every slot holding that asset type:
    responsive_creative.json ``asset_image_max_file_size_kb`` caps each image the pool holds,
    whichever slot it fills. That reading of a name the pin gives no description is this
    seller's: the role is no slot's, and the asset type is the only thing it can name. The
    pin's own description of the same cap on native_in_feed.json, ``max_image_file_size_kb``
    (:data:`_TYPED_FILE_SIZE_SUFFIX`), names exactly the format's image slots, which is that
    reading.

    A CARD slot also carries what image_carousel.json states about each card's ``media``
    (core/assets/card-asset.json: "Either an ``image`` or ``video`` asset, matching the parent
    format's ``allowed_card_media_asset_types`` parameter"): the media types it accepts
    (``media_asset_types``, :data:`_DEFAULT_CARD_MEDIA_TYPES` when unstated), the per-type
    limits ``card_<type>_<suffix>`` (``media``), and ``card_aspect_ratio``, "Aspect ratio
    shared across all cards", which lands on the slot as ``aspect_ratio`` and is asked of the
    media because a card-asset states no dimensions of its own. A card's ``cta`` is held to
    the declaration's ``cta_values``: card-asset.json, "When the parent format declares
    ``cta_values`` (allowed CTA labels), the per-card ``cta`` MUST be one of those values".
    """
    group = str(slot.get("asset_group_id") or "")
    roles = _slot_roles(group) | {f"asset_{slot.get('asset_type')}"}
    is_card = slot.get("asset_type") == "card"
    effective: dict[str, Any] = dict(slot)
    members: dict[str, Any] = {}
    media: dict[str, dict[str, Any]] = {}
    for key, bound in declared.items():
        if bound is None:
            continue
        constraint, role = _flat_constraint(key)
        member = _member_of(role, group)
        if role in roles:
            effective[constraint] = _narrower(constraint, effective.get(constraint), bound)
        elif constraint == "max_chars" and member:
            members[member] = bound
        elif is_card and constraint and member in _CARD_MEDIA_TYPES:
            media.setdefault(member, {})[constraint] = bound
    if members:
        effective["member_max_chars"] = members
    if is_card:
        effective["media_asset_types"] = declared.get("allowed_card_media_asset_types") or list(
            _DEFAULT_CARD_MEDIA_TYPES
        )
        effective["media"] = media
        if declared.get("cta_values"):
            effective["member_allowed_values"] = {"cta": declared["cta_values"]}
    return effective


def _member_of(role: str, group: str) -> str | None:
    """The member a ``<singular>_<member>`` role names on slot *group*'s assets, if it names one."""
    prefix = f"{group.removesuffix('s')}_"
    if not role.startswith(prefix):
        return None
    return role.removeprefix(prefix) or None


def _narrower(constraint: str, own: Any, flat: Any) -> Any:
    """The stricter of a slot's own *constraint* and the flat declaration param stating it."""
    if own is None:
        return flat
    if constraint == "required":
        return bool(own) or bool(flat)
    if _is_number(own) and _is_number(flat):
        return max(own, flat) if constraint.startswith("min") else min(own, flat)
    return own


#: The asset types a slot's ``max_size_kb`` is answered by, through ``file_size_bytes``:
#: ``formats/canonical/_base.json`` makes the cap valid for image, video, audio and zip, and
#: the first three state the member (core/assets/*-asset.json). A zip states none, so a cap
#: on a zip slot is a fact no member answers and is not graded.
_SIZED_ASSET_TYPES: frozenset[str] = frozenset({"image", "video", "audio"})

#: The asset types a slot's ``max_chars`` counts, through ``content``: _base.json makes the
#: limit valid for text, markdown and brief, and text-asset.json and markdown-asset.json
#: carry the characters in ``content``. A brief asset carries none.
_CHARACTER_ASSET_TYPES: frozenset[str] = frozenset({"text", "markdown"})


def _slot_constraint_shortfalls(
    slots: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], primary: str | None
) -> list[ManifestShortfall]:
    """What each filled slot fails of the constraints stated FOR THAT SLOT, every slot alike.

    ``formats/canonical/_base.json``: "per-slot constraints (a specific slot's ``max_chars``
    or ``max_size_kb``) live on the slot entry", and "Format-level narrowing parameters that
    apply across all slots (e.g., flat ``headline_max_chars``...) may also live on the format
    declaration". The declaration's other params describe the main media asset and are
    graded on it (:func:`_manifest_shortfalls`); these are the ones that reach the rest:

    * ``min`` / ``max`` -- "count for repeatable / pool slots", the number of assets the slot
      holds (one for a single object);
    * ``max_chars`` -- the length of a text or markdown asset's ``content``, and of a card's
      ``headline`` or ``description`` (``member_max_chars``);
    * ``max_size_kb`` -- "exact kilobytes, where 1 KB = 1,000 bytes", against
      ``file_size_bytes``. Silence fails, as it does for the declaration's own cap
      (:data:`_MANIFEST_MUST_BE_STATED`): a cap the manifest does not answer cannot be checked;
    * ``pixel_ratios`` on a slot other than the main one -- a STATED ``pixel_ratio`` outside
      the set. An unstated one is not graded: the pin infers it from "the matched logical
      size", and only the main slot has one. The main slot's set is graded with its size;
    * ``aspect_ratio``, ``widths`` and ``heights`` on a slot other than the main one
      (:func:`_slot_dimension_shortfalls`).

    *slots* are effective slots (:func:`_effective_slot`): each carries the flat declaration
    params that name its role, so a limit stated either way is read here once.

    An empty slot is :func:`_manifest_slot_shortfalls`' to report; a slot holding an array
    is graded item by item, its path indexed as a rendition set's is.
    """
    shortfalls: list[ManifestShortfall] = []
    for slot in slots:
        group = str(slot.get("asset_group_id") or "")
        if group not in manifest:
            continue
        value = manifest[group]
        count = len(value) if isinstance(value, list) else 1
        for bound, failed in ((slot.get("min"), "min"), (slot.get("max"), "max")):
            if _is_number(bound) and (count < bound if failed == "min" else count > bound):
                shortfalls.append(ManifestShortfall(f"assets.{group}", failed, str(count), (_shown(bound),)))
        items = value if isinstance(value, list) else [value]
        for index, asset in enumerate(items):
            if isinstance(asset, Mapping):
                path = f"{group}[{index}]" if isinstance(value, list) else group
                shortfalls.extend(_slot_asset_shortfalls(path, asset, slot, main=group == primary))
    return shortfalls


def _slot_asset_shortfalls(
    path: str, asset: Mapping[str, Any], slot: Mapping[str, Any], *, main: bool
) -> list[ManifestShortfall]:
    """One asset of a slot against that slot's own constraints (:func:`_slot_constraint_shortfalls`)."""
    asset_type = str(asset.get("asset_type"))
    shortfalls: list[ManifestShortfall] = []
    texts = [(member, asset.get(member), bound) for member, bound in (slot.get("member_max_chars") or {}).items()]
    if asset_type in _CHARACTER_ASSET_TYPES:
        texts.append(("content", asset.get("content"), slot.get("max_chars")))
    shortfalls.extend(
        ManifestShortfall(f"assets.{path}.{member}", "max_chars", str(len(text)), (_shown(bound),))
        for member, text, bound in texts
        if isinstance(text, str) and _is_number(bound) and len(text) > bound
    )
    shortfalls.extend(_closed_value_shortfalls(path, asset, slot))
    if not main and asset_type in _DIMENSIONED_ASSET_TYPES:
        shortfalls.extend(_slot_dimension_shortfalls(path, asset, slot))
    if asset_type in _DIMENSIONED_ASSET_TYPES:
        # responsive_creative.json ``logo_aspect_ratios``, a SET of ratios. Graded on the main
        # slot as well as the others: the main slot's own params state no such set, and a
        # declaration whose only required media slot is the logo makes the logo the main slot.
        shortfalls.extend(_aspect_shortfalls(path, asset, slot.get("aspect_ratios")))
    shortfalls.extend(_duration_bound_shortfalls(path, asset, slot))
    if asset_type == "card" and isinstance(media := _plain(asset.get("media")), Mapping):
        shortfalls.extend(_card_media_shortfalls(f"{path}.media", media, slot))
    size_cap = slot.get("max_size_kb")
    if asset_type in _SIZED_ASSET_TYPES and _is_number(size_cap):
        size = _plain(asset.get("file_size_bytes"))
        if not _is_number(size) or size / 1000 > size_cap:
            shortfalls.append(
                ManifestShortfall(
                    f"assets.{path}.file_size_bytes",
                    "max_size_kb",
                    None if size is None else _shown(size),
                    (_shown(size_cap),),
                )
            )
    if not main and asset_type == "image" and (ratios := slot.get("pixel_ratios")):
        shortfalls.extend(
            _set_shortfalls(path, "pixel_ratio", "pixel_ratios", _plain(asset.get("pixel_ratio")), ratios)
        )
    return shortfalls


#: The asset types whose ``width`` and ``height`` a slot's aspect ratio and size sets are
#: asked of (core/assets/image-asset.json, video-asset.json).
_DIMENSIONED_ASSET_TYPES: frozenset[str] = frozenset({"image", "video"})


def _slot_dimension_shortfalls(path: str, asset: Mapping[str, Any], slot: Mapping[str, Any]) -> list[ManifestShortfall]:
    """A non-main slot's asset against the slot's aspect ratio and accepted widths and heights.

    Both reach the slot only as flat params (:func:`_effective_slot`): audio_hosted.json
    ``companion_image_aspect_ratio``, responsive_creative.json ``images_landscape_aspect_ratio``,
    video_hosted.json ``companion_banner_widths`` ("Permitted companion banner widths") and
    ``companion_banner_heights``. The ratio is graded as the main asset's is
    (:func:`_aspect_shortfalls`); a stated width or height outside its set is a size the slot
    does not accept (:func:`_set_shortfalls`). An asset silent about a dimension is not graded on it.
    """
    shortfalls = _aspect_shortfalls(path, asset, slot.get("aspect_ratio"))
    for constraint, member in (("widths", "width"), ("heights", "height")):
        shortfalls.extend(_set_shortfalls(path, member, member, _plain(asset.get(member)), slot.get(constraint)))
    return shortfalls


def _closed_value_shortfalls(path: str, asset: Mapping[str, Any], slot: Mapping[str, Any]) -> list[ManifestShortfall]:
    """A text value outside the closed set its slot accepts (:data:`_CLOSED_VALUE_CONSTRAINT`).

    Two places carry one: a text asset's ``content`` in a slot the declaration closes
    (``cta_values`` on the ``cta`` slot, core/assets/text-asset.json ``content``), and a
    string member of each asset a slot holds (``member_allowed_values``, a card's ``cta``).
    Matched exactly, as the pin's examples spell the values (``LEARN_MORE``, ``SHOP_NOW``):
    no case folding, because "any value outside that list is rejected by definition"
    (enums/error-code.json CREATIVE_VALUE_NOT_ALLOWED). A slot left empty is not graded here;
    a closed set constrains a value, it does not require one.
    """
    checks = [
        (member, asset.get(member), values) for member, values in (slot.get("member_allowed_values") or {}).items()
    ]
    if asset.get("asset_type") == "text":
        checks.append(("content", asset.get("content"), slot.get(_CLOSED_VALUE_CONSTRAINT)))
    return [
        shortfall
        for member, value, values in checks
        for shortfall in _set_shortfalls(path, member, _CLOSED_VALUE_CONSTRAINT, value, values)
    ]


#: The asset types whose ``duration_ms`` a slot's duration bounds are asked of
#: (core/assets/video-asset.json, audio-asset.json).
_TIMED_ASSET_TYPES: frozenset[str] = frozenset({"video", "audio"})


def _duration_bound_shortfalls(path: str, asset: Mapping[str, Any], slot: Mapping[str, Any]) -> list[ManifestShortfall]:
    """A video or audio asset whose ``duration_ms`` lies outside its slot's duration bounds.

    responsive_creative.json ``video_min_duration_ms`` / ``video_max_duration_ms`` on the
    ``video`` pool and image_carousel.json ``card_video_max_duration_ms`` on a card's video
    media (:func:`_effective_slot`). Each bound is decided by :func:`_constraint_unsatisfied`,
    which owns what a ``min_`` / ``max_`` bound means. An asset that states no duration is not
    graded: only a fixed duration is among what a creative must state (:data:`_MUST_BE_STATED`).
    """
    duration = _plain(asset.get("duration_ms"))
    if asset.get("asset_type") not in _TIMED_ASSET_TYPES or not _is_number(duration):
        return []
    return [
        ManifestShortfall(f"assets.{path}.duration_ms", constraint, _shown(duration), (_shown(slot[constraint]),))
        for constraint in ("min_duration_ms", "max_duration_ms")
        if _constraint_unsatisfied(constraint, slot.get(constraint), duration)
    ]


def _card_media_shortfalls(path: str, media: Mapping[str, Any], slot: Mapping[str, Any]) -> list[ManifestShortfall]:
    """A card's ``media`` against what the declaration states about every card's media.

    The asset type first: card-asset.json's ``media`` is an image or video "matching the parent
    format's ``allowed_card_media_asset_types`` parameter", so a type outside it is refused and
    nothing else about the media is graded. Then the media is graded as an asset of a slot
    whose constraints are the card slot's ``aspect_ratio`` (``card_aspect_ratio``) and the
    limits stated for its type (``card_image_max_file_size_kb``, ``card_video_max_file_size_kb``
    -- "1 KB = 1,000 bytes" -- and ``card_video_max_duration_ms``), through the same reader
    every other slot's asset goes through.
    """
    media_type = str(media.get("asset_type"))
    accepted = tuple(slot.get("media_asset_types") or ())
    if refused := _set_shortfalls(path, "asset_type", "allowed_card_media_asset_types", media_type, accepted):
        return refused
    media_slot = {"aspect_ratio": slot.get("aspect_ratio"), **(slot.get("media") or {}).get(media_type, {})}
    return _slot_asset_shortfalls(path, media, media_slot, main=False)


def _accepted_ratios(declared: Any, slot: Any) -> list[Any]:
    """The pixel ratios an image slot accepts: its own set narrowed by the declaration's.

    ``formats/canonical/image.json`` ``pixel_ratios``: "Absence means ``[1]`` for backward
    compatibility". ``formats/canonical/_base.json`` slot ``pixel_ratios``: "When the image
    canonical also declares top-level ``params.pixel_ratios``, the effective set is the
    intersection" -- so a declaration silent about density and a slot stating ``[1, 2]``
    accepts both, and the ``[1]`` default applies only where neither states a set.
    """
    if slot is None:
        return list(declared) if declared is not None else [1]
    return [ratio for ratio in slot if declared is None or ratio in declared]


def _graded_rendition(
    path: str, asset: Mapping[str, Any], declared: Mapping[str, Any]
) -> tuple[float, list[ManifestShortfall]]:
    """One asset graded at its best candidate ratio: that ratio, and what it fails there."""
    graded = [(ratio, _asset_shortfalls(path, asset, declared, ratio)) for ratio in _candidate_ratios(asset, declared)]
    return min(graded, key=lambda pair: len(pair[1]))


def _coverage_shortfalls(
    slot: str, required: Any, graded: Sequence[tuple[str, float, list[ManifestShortfall]]]
) -> list[ManifestShortfall]:
    """What a rendition set fails of the slot's ``required_pixel_ratios``, as values.

    ``formats/canonical/_base.json``: "exactly one matching image rendition for each required
    ratio. Other accepted ratios remain optional", and "SDKs enforce intersection, subset,
    coverage, and duplicate-ratio rules". A required ratio no rendition was graded at is
    MISSING, reported at ``assets.<slot>.pixel_ratio`` with nothing stated and that ratio
    accepted; a ratio two renditions were graded at is a DUPLICATE, reported at the second
    one with the required set accepted. A rendition failing its own size is reported by its
    own shortfall and still counts here, so a wrong-sized 2x is a size problem and not also
    a missing 2x. None of these is ``CREATIVE_SIZE_MISMATCH``: every submitted rendition may
    match an accepted size and the set still be incomplete, so the constraint is not one of
    :data:`_DIMENSION_CONSTRAINTS`.
    """
    if not required:
        return []
    seen: list[float] = []
    shortfalls: list[ManifestShortfall] = []
    for path, ratio, _failed in graded:
        if ratio in seen:
            shortfalls.append(
                ManifestShortfall(
                    f"assets.{path}.pixel_ratio", "required_pixel_ratios", _shown(ratio), _shown_all(required)
                )
            )
        seen.append(ratio)
    missing = [
        ManifestShortfall(f"assets.{slot}.pixel_ratio", "required_pixel_ratios", None, (_shown(ratio),))
        for ratio in required
        if ratio not in seen
    ]
    return [*missing, *shortfalls]


def _asset_shortfalls(
    slot: str, asset: Mapping[str, Any], declared: Mapping[str, Any], ratio: float
) -> list[ManifestShortfall]:
    """What the asset in *slot* fails in *declared* at pixel *ratio*, as values.

    ``rejected_value`` is what the asset STATES -- the intrinsic member, not the logical size
    it was compared as -- because that is the value the buyer can find in what they sent.
    """
    claim, members = _asset_claim(asset, ratio)
    return [
        ManifestShortfall(
            field=f"assets.{slot}.{members[constraint]}",
            constraint=constraint,
            rejected_value=None if asset.get(members[constraint]) is None else _shown(asset.get(members[constraint])),
            accepted_values=_shown_all(declared[constraint]),
        )
        for constraint in _unsatisfied_params(
            claim, _stated_constraints(declared, claim), must_state=_MANIFEST_MUST_BE_STATED
        )
    ]


def _manifest_verdict(
    graded_against: str,
    shortfalls: Sequence[ManifestShortfall],
    selection: tuple[LibraryFormatDeclaration, ...] = (),
) -> FormatSatisfaction:
    """Satisfied with *selection* when nothing fell short, else the shortfalls as a refusal.

    *graded_against* is the label the refusal names in ``accepted_values``: the declaration
    the manifest was held to, or the canonical kind when no declaration was selected.
    """
    if not shortfalls:
        return FormatSatisfaction(satisfied=True, selection=selection)
    return FormatSatisfaction(
        satisfied=False,
        refusal=FormatRefusal.MANIFEST_NOT_SATISFIED,
        accepted_values=(graded_against,),
        summary=(
            f"manifest does not satisfy {graded_against!r} on: {', '.join(shortfall.field for shortfall in shortfalls)}"
        ),
        shortfalls=tuple(shortfalls),
    )


def kind_manifest_verdict(kind: str, assets: Any) -> FormatSatisfaction:
    """A canonical manifest graded against its KIND alone, when no declaration is selected.

    A ``sync_creatives`` that assigns a creative to nothing selects no product declaration,
    so what the manifest can be held to is the canonical format its ``format_kind`` names:
    the slots that kind's schema declares (:func:`declared_slots_by_kind`). A canonical
    schema fixes no dimensions or durations by default, so the slots are the whole of it.
    Graded by the same slot reader as a selected declaration, so the two cannot disagree
    about what a slot is.
    """
    return _slots_verdict(kind, declared_slots_by_kind().get(kind, ()), assets)


def named_format_manifest_verdict(spec: Format, assets: Any) -> FormatSatisfaction:
    """A LEGACY manifest graded against the format its ``format_id`` names, as the agent serves it.

    creative-manifests.mdx § Validation steps 2 and 3 -- "Confirm all required slots are
    present" and "Validate each asset against its declared type" -- for the deprecated branch,
    where the declaration is the named format's ``assets[]``. core/format.json (3.2) states
    the keying: each asset's ``asset_id`` "must be used as the key in creative manifests",
    and ``required`` means "Required assets must be provided for a valid creative". So each
    individual asset is a slot keyed by its ``asset_id``, graded by the same slot reader as a
    canonical declaration (:func:`_manifest_slot_shortfalls`).

    Not graded, deliberately, and both are ceilings of a branch the pin retires on 2026-11-25:

    * an asset's ``requirements``: the v1 vocabulary (``min_duration_ms``, ``codecs``,
      ``formats``) is not the canonical params vocabulary :func:`_unsatisfied_params` reads,
      so grading it needs a translation layer for a shape on its way out;
    * a ``repeatable_group``: core/format.json does not say how a manifest keys the
      repetitions of a group, so there is no key to look a group's assets up by.
    """
    slots = tuple(
        {
            "asset_group_id": asset.asset_id,
            "asset_type": str(asset.asset_type),
            "required": asset.required,
            "requirements": getattr(asset, "requirements", None),
        }
        for asset in spec.assets or ()
        if isinstance(asset, LibraryIndividualAsset)
    )
    return _slots_verdict(spec.format_id.id, slots, assets)


def _slots_verdict(graded_against: str, slots: Sequence[Mapping[str, Any]], assets: Any) -> FormatSatisfaction:
    """*assets* graded against *slots* alone: required slots present, each filled slot's type."""
    return _manifest_verdict(graded_against, _manifest_slot_shortfalls(slots, manifest_document(assets)))


def _selecting(
    candidates: Sequence[LibraryFormatDeclaration],
    params: Mapping[str, Any] | None,
    *,
    exact: bool,
    manifest: Mapping[str, Any] | None = None,
) -> FormatSatisfaction:
    """Grade every candidate; the verdict selects EACH one the claim satisfies.

    ``media-buy/package-request.json`` step (2) derives "the product ``format_options[]``
    entries selected by each route", plural, and compares the sets. So a route over
    several candidates selects all of those it satisfies, in declaration order, and the
    first of them is the one a package records. When none satisfies, the FIRST candidate's
    refusal is reported: it names the params that failed, which is the buyer's lever, and a
    union across candidates would be a sentence this module does not author.
    """
    graded = [_graded(candidate, params, exact=exact, manifest=manifest) for candidate in candidates]
    selection = tuple(declaration for verdict in graded for declaration in verdict.selection)
    return FormatSatisfaction(satisfied=True, selection=selection) if selection else graded[0]


def _legacy_route(
    identity: FormatSelector,
    declarations: Sequence[LibraryFormatDeclaration],
    params: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None = None,
) -> FormatSatisfaction:
    """A retained legacy reference: normalize it, then see which declaration it selects.

    TWO normalization steps, in the pin's order, and both of them run v1 -> v2 only -- the
    direction ``registries/v1-canonical-mapping.json`` calls "authoritative", the one a
    seller MUST NOT invert:

    1. The declaration's OWN assertion. ``v1_format_ref[]`` is the seller saying which
       legacy id this canonical declaration projects to, so a match there is a lookup and
       not a guess. The comparison is ``format_resolver.format_identity``'s ``(agent_url,
       id)`` pair -- THE one implementation of it in this codebase, including the
       canonicalization ``core/format-id.json`` makes a MUST before two references may be
       treated as one. The SDK's ``find_declaration_by_v1_format_id`` answers the same
       question but reads a ``legacy_format_refs`` property that the generated union arms
       do not carry (they declare ``v1_format_ref``), so it matches nothing on the type
       ``Product.format_options`` actually holds -- measured, not assumed.
    2. The registry's parameterized upgrade. ``display_300x250_image`` upgrades to
       ``display_image`` + ``width=300`` + ``height=250``, which is what makes the
       storyboard's ``create_media_buy_with_legacy_mrec_format`` step a satisfaction
       question rather than a string comparison. This is the step
       ``core/product-format-declaration.json`` requires -- "Legacy named formats MUST be
       normalized to canonical declarations before comparison; do not exact-match raw
       ``(agent_url, id)`` pairs once a ``format_id`` has been projected".

    EVERY DECLARATION EITHER STEP MATCHES IS SELECTED, not the first one met.
    ``media-buy/package-request.json`` step (2) compares the SETS of entries each route
    selects, and adds that "legacy-to-canonical parameter compatibility uses the asymmetric
    v2 narrows v1 relation, not raw object equality". So step 2 selects every same-kind
    declaration that narrows the projection: each parameter the projection STATES is
    graded by :func:`_unsatisfied_params` against the declaration, and one it leaves
    unstated is open (:func:`_stated_constraints`). A broad ``display_image`` beside two
    image options therefore selects both, and beside an option ref naming one of them the
    selected sets differ -- CONFLICTING_SELECTORS, as step (3) says of "different ...
    selected option sets".

    THE THREE OUTCOMES, which is what this function answers rather than "the declaration
    or None". ``media-buy/package-request.json`` separates resolution from selection and
    gives them different codes, so collapsing them loses the distinction the storyboard's
    two legacy rejections are built on:

    * one of the steps SELECTS a declaration -- the route resolved and selected it;
    * the reference NORMALIZES (projected params, or a kind from
      ``canonical_kind_for``) but lands on no declaration this product publishes -- the
      route resolved and selected NOTHING, which is ``LEGACY_NOT_SELECTED``. Alone that is
      "a single resolved route that does not satisfy the product", UNSUPPORTED_FEATURE;
      beside a route that DID select one it is a selected-set mismatch, and the pin is
      explicit that "canonical precedence must not hide the dimensional conflict". This is
      the storyboard's ``reject_conflicting_dual_emission``: measured on the pinned wheel,
      ``display_728x90`` upgrades to ``display_image`` + 728x90 and
      ``canonical_kind_for`` answers ``image``, so against a product fixing 300x250 it
      normalizes and selects nothing;
    * neither step normalizes it at all -- a RESOLUTION failure, which is the sixth
      normative step (fail closed) and the storyboard's
      ``reject_unprojectable_legacy_dual_emission``.

    The middle outcome is the one this function used to lose. Returning ``None`` for both
    of the last two let :func:`_canonical_lane` fall through to the bare-kind route with
    ``params=None``, which skips the params grading entirely and accepted any declaration
    publishing that kind -- so a package naming ``display_728x90`` against the 300x250
    product resolved to the 300x250 declaration and was accepted (salesagent-t0uoo).

    WHEN THE REFERENCE NORMALIZES TO A KIND AND NOTHING ELSE, the kind route grades it.
    ``display_static`` and a bare ``display_image`` project to ``image`` with no params, so
    what the normalization produced (a kind) is what is graded. The params are the
    projection's (:func:`_legacy_projection`), not only the SDK's size upgrade: measured,
    ``video_640x480`` and ``video_30s`` are not upgraded at all, yet the registry states
    640x480 and ``duration_ms_exact=30000`` for them, and they are graded on those.

    The mirror case USED to be ``display_300x250_image``, on the measurement that
    ``canonical_kind_for`` raised for it while the upgrade yielded 300x250. That asymmetry
    was a defect in the projection, not a property of the registry: it skipped the upgrade
    before matching the glob, so a size-encoded id never reached the ``display_image``
    mapping it belongs to. Fixed in salesagent-iz7dm, where it was 101 of the 376 nodeids
    regressed on land. The id now answers ``image`` in both directions.
    """
    from src.core.format_resolver import format_identity_or_none

    assertion = identity.assertion
    assert assertion is not None, "the legacy route is reached only for a claim asserting a reference"
    # ``None`` is a reference whose agent_url the canonicalizer refused (FormatSelector.of_legacy).
    # It must not equal an unusable v1_format_ref, which ``format_identity_or_none`` also maps to None.
    asserted = identity.legacy
    asserting = [
        declaration
        for declaration in declarations
        if asserted is not None
        and any(format_identity_or_none(ref) == asserted for ref in declaration.v1_format_ref or [])
    ]
    if asserting:
        return _selecting(asserting, params, exact=True, manifest=manifest)

    projected = _legacy_projection(assertion)
    kind = identity.kind
    if not projected:
        if kind is None:
            return FormatSatisfaction(
                satisfied=False,
                refusal=FormatRefusal.LEGACY_UNPROJECTABLE,
                rejected_value=assertion.id,
                accepted_values=tuple(sorted(_declaration_label(d) for d in declarations)),
                summary=(f"named format {assertion.id!r} projects to no canonical declaration this product publishes"),
            )
        return _kind_route(kind, declarations, params, manifest)

    narrowing = [
        declaration
        for declaration in declarations
        if _narrows(str(declaration.format_kind), _declared_params(declaration), kind, projected)
    ]
    if narrowing:
        return _selecting(narrowing, params, exact=True, manifest=manifest)
    return FormatSatisfaction(
        satisfied=False,
        refusal=FormatRefusal.LEGACY_NOT_SELECTED,
        rejected_value=assertion.id,
        accepted_values=tuple(sorted(_declaration_label(d) for d in declarations)),
        summary=(f"named format {assertion.id!r} normalizes to a canonical declaration this product does not publish"),
    )


def _narrows(
    declared_kind: str, declared_params: Mapping[str, Any], kind: str | None, projected: Mapping[str, Any]
) -> bool:
    """Whether a declaration narrows a legacy reference that normalized to *kind* + *projected*.

    canonical-formats.mdx § "Narrows" -- formal definition (normative): every v2 parameter
    must fall inside the matching v1 requirement, and "any v2 parameter value that falls
    outside the corresponding v1 constraint is a *conflict*, not narrowing". A kind the
    reference does not project to (``None``) and a parameter it does not state are open: the
    v1 baseline says nothing there, so the declaration narrows into it.
    """
    return (kind is None or declared_kind == kind) and not _unsatisfied_params(
        projected, _stated_constraints(declared_params, projected)
    )


def declaration_narrows(format_kind: Any, params: Any, reference: LibraryFormatId) -> bool:
    """Whether a declaration of *format_kind* with *params* narrows the v1 format *reference* names.

    ``core/product-format-declaration.json`` on ``v1_format_ref`` (3.2.7): "The v2
    declaration's ``params`` MUST narrow (be compatible with) each referenced v1 format's
    ``requirements``". A declaration asserting a reference it does not narrow is the
    divergence ``enums/error-code.json`` names ``FORMAT_DECLARATION_DIVERGENT`` ("different
    canonical, different dimensions"), and canonical-formats.mdx's producer rules answer it:
    "A producer that cannot guarantee agreement MUST omit the legacy compatibility
    projection." ``format_resolver`` asks this of every asserted reference before projecting it.

    The same rule the legacy selector route applies (:func:`_narrows`), so a reference the
    product would not project is one the route would not select through the registry either.
    The reference is normalized v1 -> v2 only, the direction the registry makes authoritative;
    a reference that normalizes to nothing (a seller-bespoke id) states no constraint and is
    taken as asserted.

    *format_kind* and *params* are read off a stored document or a validated model alike.
    """
    from src.core.format_resolver import canonical_kind_for

    try:
        kind: str | None = canonical_kind_for(reference)
    except ValueError:
        kind = None
    declared = _plain(params)
    return _narrows(
        str(_plain(format_kind)),
        declared if isinstance(declared, Mapping) else {},
        kind,
        _legacy_projection(reference),
    )


def _legacy_projection(assertion: LibraryFormatId) -> dict[str, Any]:
    """The canonical params a legacy reference states once the registry projects it.

    docs/creative/canonical-formats.mdx § Format matching vs product satisfaction (v3.2.7):
    a legacy ``format_id`` resolves "through ... the canonical mapping registry to a
    canonical declaration ... After that projection, implementations compare the canonical
    shape and parameters". So the params are the PROJECTION's
    (``format_resolver.canonical_projection``), not only the SDK's size upgrade:
    ``display_300x250_image`` states ``width=300`` + ``height=250``, ``video_30s`` and
    ``video_pre_roll_30s`` each state ``duration_ms_exact=30000``, and a broad
    ``display_image`` or ``display_static`` states nothing and is narrowed by every
    same-kind declaration. Reading only the upgrade left ``video_30s`` with no params, so it
    was graded as a bare ``video_hosted`` kind and refused by a product fixing 30 seconds.

    IN THE DECLARATION'S VOCABULARY: a reference's one fixed ``duration_ms`` is graded as
    ``duration_ms_exact``, which :func:`_unsatisfied_params` also holds inside a declared
    range. Empty when the registry fails closed for the reference; the route then refuses
    it or grades the kind it has.

    The id alone is projected, as a stored creative's selector carries no served
    declaration; the KIND on the package route is projected with it
    (:func:`selector_routes`).
    """
    from src.core.format_resolver import canonical_projection

    try:
        return canonical_projection(assertion).params
    except ValueError:
        return {}


def _graded(
    selected: LibraryFormatDeclaration,
    params: Mapping[str, Any] | None,
    *,
    exact: bool,
    manifest: Mapping[str, Any] | None = None,
) -> FormatSatisfaction:
    """*selected* is the declaration the route picked; grade the claim's params against it.

    ``exact`` says whether the ROUTE proved the identity by itself. An exact option ref and
    a resolved legacy ref each name ONE declaration, so there is nothing left for the claim
    to narrow and a claim stating no params is complete -- the declaration's constraints the
    claim is SILENT about are dropped, which still refuses a buyer who names an option and
    then contradicts it with ``width: 728``. A bare kind proves nothing (``formats.mdx``'s
    fifth rule is exactly that), so on that route the whole declaration is the obligation
    and silence about an exact dimension fails.

    Without that distinction ``create_media_buy_with_canonical_format_option`` (an option
    ref, no params, ACCEPT) and ``reject_bare_image_selector_for_fixed_mrec`` (a kind, no
    params, REJECT) are the same input -- which is what this module's probe reported before
    the distinction existed.

    ``params is None`` skips the params grading: the claim did not state its params at all,
    which is every stored creative. A canonical creative is graded on its MANIFEST instead,
    whichever route selected the declaration -- an option ref names the declaration, and the
    manifest is still owed every constraint of it (creative-manifests.mdx § Validation: the
    manifest is validated "against the selected product or publisher declaration").

    The satisfied verdict CARRIES ``selected``, so a caller holding two route resolutions
    can ask whether they named the same declaration. That is steps 11 and 12.
    """
    if manifest is not None:
        return _manifest_verdict(_declaration_label(selected), _manifest_shortfalls(selected, manifest), (selected,))
    resolved = FormatSatisfaction(satisfied=True, selection=(selected,))
    if params is None:
        return resolved
    declared = _declared_params(selected)
    constraints = _stated_constraints(declared, params) if exact else declared
    if not (failed := _unsatisfied_params(params, constraints)):
        return resolved
    return FormatSatisfaction(
        satisfied=False,
        refusal=FormatRefusal.PARAMS_NOT_SATISFIED,
        rejected_value=str(selected.format_kind),
        accepted_values=tuple(sorted(failed)),
        summary=(
            f"selector for {selected.format_kind!r} does not satisfy "
            f"{_declaration_label(selected)!r} on: {', '.join(sorted(failed))}"
        ),
    )


def _option_route(
    option: FormatOptionIdentity,
    declarations: Sequence[LibraryFormatDeclaration],
    params: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None = None,
) -> FormatSatisfaction:
    """An EXACT publisher-or-product option claim, which must match both halves.

    ``formats.mdx``: "An exact publisher claim must match both ``publisher_domain`` and
    ``format_option_id``". :class:`FormatOptionIdentity` is the type that holds the pair
    together, so identities are compared and never their parts -- which is what keeps a
    publisher-catalogue option and a product-local option sharing one ``format_option_id``
    from being taken for each other. They are different options; the pin's two arms make
    ``publisher_domain`` required on one and forbidden on the other.
    """
    selected = next((d for d in declarations if _option_identity(d) == option), None)
    if selected is None:
        return FormatSatisfaction(
            satisfied=False,
            refusal=FormatRefusal.OPTION_NOT_PUBLISHED,
            rejected_value=option.format_option_id,
            accepted_values=tuple(sorted(_declaration_label(d) for d in declarations)),
            summary=f"format option {option.format_option_id!r} is not published by this product",
        )
    return _graded(selected, params, exact=True, manifest=manifest)


def _kind_route(
    kind: str,
    declarations: Sequence[LibraryFormatDeclaration],
    params: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None = None,
) -> FormatSatisfaction:
    """A bare canonical kind: the closed-set step, then the full params obligation.

    THE CLOSED-SET RULE AND ITS ERROR ARE THE SDK'S.
    ``core/product-format-declaration.json``'s ``seller_preference`` is normative that
    ``format_options[]`` "IS the closed set of accepted formats; anything outside the list
    is rejected at ``create_media_buy`` regardless of preference", and the SDK states it as
    ``validate_format_kind_in_options`` raising
    :class:`FormatKindNotInClosedSetError` -- whose ``format_kind`` + ``accepted_kinds``
    members are what its own ``to_wire_error`` turns into ``details.rejected_value`` +
    ``details.accepted_values``. That error is raised here, so the wire shape is the SDK's
    and so is the ``accepted`` set: the PRODUCT's kinds, not the pinned vocabulary's, which
    is what makes a kind in the sixteen but absent here still a refusal.

    THE COMPARISON runs here rather than inside the SDK function, and that is forced, not
    preferred. The SDK helper is typed ``Iterable[Format]`` --
    ``adcp.types.canonical_creative.Format``, whose ``params`` is an untyped dict -- while
    ``Product.format_options`` holds the SIXTEEN-ARM
    ``ProductFormatDeclaration`` union, whose ``params`` is the typed per-kind model. mypy
    rejects the call outright (``Argument 2 ... has incompatible type``), and so does its
    sibling ``find_declaration_by_kind``. The alternatives were a ``type: ignore`` (the
    ratchet only shrinks) or a ``cast`` asserting a class relationship that does not exist;
    the comparison itself is ``wanted in accepted``, so stating it over the type the seller
    actually holds costs two lines and keeps both the rule and the error where they belong.
    Filed as salesagent-ol033.17, with the same mismatch measured from the runtime side on
    ``find_declaration_by_v1_format_id``.

    EVERY DECLARATION PUBLISHING THE KIND IS A CANDIDATE, which is work item M2. A product
    may publish several of one kind -- that is the case ``format_option_id`` exists for,
    "REQUIRED when a product contains multiple declarations with the same format_kind"
    (``core/product-format-declaration.json``) -- and the pin's step (2) asks which
    ``format_options[]`` entries the route selects "using directional product
    satisfaction", so the selection is the one the claim SATISFIES rather than the first
    one declared. Taking the first refused ``{image, 728x90}`` against a product
    publishing image 300x250 AND image 728x90, telling the buyer the product cannot do
    what it published (salesagent-ky23p).

    Every candidate the claim satisfies is SELECTED, and step (2) compares those sets
    across routes; see :func:`_selecting`, which also says which refusal is reported when
    none satisfies.

    A MANIFEST NAMING NO OPTION NEEDS A KIND THE PRODUCT PUBLISHES ONCE. A package selector
    may select several declarations (step (2) compares sets), but a creative is validated
    "against the selected product or publisher declaration" -- one. ``canonical-formats.mdx``'s
    routing rule, marked normative: "``format_option_ref`` is **REQUIRED** on the manifest
    when the target product's ``format_options`` contains two or more declarations sharing
    the same ``format_kind``", and "**OPTIONAL** when each ``format_kind`` in the product's
    ``format_options`` is unique". ``core/creative-asset.json`` and
    ``core/creative-manifest.json`` say the same of the field, and ``sync_creatives.mdx``
    "Required when the target product has multiple options with the same format_kind".
    creative-manifests.mdx § Validation step 1, "otherwise select the unambiguous compatible
    ``format_kind`` declaration", is the optional case: the kind alone names one declaration,
    and it must be compatible. So with a manifest and several candidates the candidates are
    graded, and:

    * none compatible -- the first candidate's shortfalls are reported, naming every
      candidate in ``accepted_values``: the assets fail whichever option the buyer names;
    * one or more compatible -- :attr:`FormatRefusal.MANIFEST_AMBIGUOUS`, the missing ref.
      Fitting one of them does not stand in for naming it: the rule turns on what the
      product publishes, not on what the assets happen to fit, and step 5 is "do not
      silently relax the target contract". ``accepted_values`` names the compatible
      declarations, which are the values the buyer can put in the ref and be accepted.
    """
    accepted = [str(declaration.format_kind) for declaration in declarations]
    if kind not in accepted:
        refused = FormatKindNotInClosedSetError(kind, accepted)
        return FormatSatisfaction(
            satisfied=False,
            refusal=FormatRefusal.KIND_NOT_PUBLISHED,
            rejected_value=refused.format_kind,
            accepted_values=tuple(sorted(set(refused.accepted_kinds))),
            summary=str(refused),
        )
    candidates = [d for d in declarations if str(d.format_kind) == kind]
    verdict = _selecting(candidates, params, exact=False, manifest=manifest)
    if manifest is None or len(candidates) == 1:
        return verdict
    if not verdict:
        return replace(verdict, accepted_values=tuple(sorted(_declaration_label(d) for d in candidates)))
    compatible = tuple(sorted(_declaration_label(d) for d in verdict.selection))
    return FormatSatisfaction(
        satisfied=False,
        refusal=FormatRefusal.MANIFEST_AMBIGUOUS,
        rejected_value=kind,
        accepted_values=compatible,
        summary=f"manifest of {kind!r} names no format_option_ref among {len(candidates)} declarations: {compatible}",
    )


def _canonical_lane(
    identity: FormatSelector,
    declarations: Sequence[LibraryFormatDeclaration],
    params: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None = None,
) -> FormatSatisfaction:
    """Grade a claim against a product that declares ``format_options[]``.

    The arms are tried in the pin's precedence and are not interchangeable: an exact option
    ref routes to one declaration, a legacy reference projects onto one, and a bare kind
    only discriminates. EXACTLY ONE arm answers -- a claim asserting a legacy reference is
    answered by the legacy route whatever else it carries, because the reference is the
    more specific identity and :func:`_legacy_route` falls back to the kind route itself
    where the reference normalized to nothing more than a kind. Letting the kind arm
    answer AFTER the legacy arm declined is what accepted a dimensionally-conflicting
    reference (salesagent-t0uoo).
    """
    if (option := identity.option) is not None:
        return _option_route(option, declarations, params, manifest)
    if identity.assertion is not None:
        return _legacy_route(identity, declarations, params, manifest)
    if (kind := identity.kind) is not None:
        return _kind_route(kind, declarations, params, manifest)
    # The claim names no kind, no option and no reference at all, so there is nothing to
    # compare against a canonical declaration. Satisfied -- and this is the SKIP-VS-REFUSE
    # DIVERGENCE resolved: the create gate skipped here while the update and assignment
    # gates refused, so one buyer input had two answers depending on which tool received
    # it. One function means one answer, and the permissive one is the one the pin supports:
    # a package naming no format "selects all formats supported by the product"
    # (media-buy/package-request.json), and a stored creative always names at least its kind.
    return _SATISFIED


def _projected_declaration(reference: LibraryFormatId) -> LibraryFormatDeclaration | None:
    """The canonical declaration the SDK projects one deprecated ``format_ids`` entry to, or None.

    ``adcp.canonical_formats.projection.project_legacy_format_id`` is the projection
    ``project_legacy_product`` applies to each entry of a product's ``format_ids``, in the
    registry's order (``registries/v1-canonical-mapping.json``), and it fails CLOSED: an
    entry it cannot map yields a ``FORMAT_PROJECTION_FAILED`` diagnostic and no declaration.
    The per-entry function and not the product one because ``project_legacy_product`` ends by
    validating a whole wire ``Product``, and the gate holds a product's two format arms, not a
    product. None is that closed failure, and so is an ``agent_url`` the SDK's own
    canonicalization refuses: either way the entry has no canonical shape to compare.

    Canonically addressed first, as :func:`_legacy_projection` is and for its reason: the
    SDK's agent comparison applies only part of ``docs/reference/url-canonicalization``.
    Read into the pinned union through ``from_attributes``, so a projected declaration is
    graded exactly as a published one is, defaults included.
    """
    from src.core.format_resolver import canonically_addressed

    try:
        projected = project_legacy_format_id(
            canonically_addressed(reference), product_id="", field="format_ids"
        ).declaration
    except ValueError:
        return None
    if projected is None:
        return None
    try:
        return _DECLARATION.validate_python(projected, from_attributes=True)
    except ValidationError:
        logger.warning("SDK projection of %r is not a pinned format declaration; treated as unprojectable", reference)
        return None


def _legacy_lane(
    identity: FormatSelector,
    legacy_references: tuple[LibraryFormatId, ...],
    projected: tuple[LibraryFormatDeclaration | None, ...],
    params: Mapping[str, Any] | None,
) -> FormatSatisfaction:
    """Grade a claim against a product that declares only the deprecated ``format_ids``.

    *projected* is the SDK's canonical projection of each entry of *legacy_references*,
    aligned by position (:func:`_projected_declaration`). It is what lets the routes of one
    package be COMPARED on this lane, which ``media-buy/package-request.json``'s selector
    contract requires of every receiver: "(1) independently resolve each present route to
    canonical declarations ... (2) derive the product format_options[] entries selected by
    each route ... and require the selected option sets to match". A legacy-only product
    publishes no ``format_options[]``, so the entries a route selects are the projections of
    the deprecated entries it is accepted by, and a direct ``format_kind`` route is graded
    against those projections with :func:`_kind_route`, which is how a bare ``image`` and a
    ``format_kind`` of another shape beside ``format_ids`` came to be accepted unexamined
    here. A kind route against a product none of whose entries project selects nothing,
    which fails closed.

    A KIND CLAIM THAT STATES NO PARAMS is still satisfied here, as before: that is a stored
    creative, whose params are unknown (see :func:`satisfies`), and grading it against
    projections it cannot state its dimensions for would refuse it on what it never claimed.

    DIRECTIONAL SATISFACTION, not identity membership, and the distinction is the pin's.
    ``core/product-format-declaration.json`` states it under "Format matching vs
    satisfaction (normative)": "do not exact-match raw ``(agent_url, id)`` pairs once a
    ``format_id`` has been projected", and "when this declaration specifies fixed
    constraints such as ``width``, ``height``, ``duration_ms_exact``, or
    ``duration_ms_range``, a buyer request or creative manifest MUST declare and satisfy
    those constraints ... a broad product MAY accept a more specific creative".

    This used to be ``claimed in legacy_keys`` over template keys, which is the comparison
    that clause forbids -- and the keys drop the parameters, so a product restricting itself
    to 300x250 and one restricting itself to nothing built the identical accepted set and a
    728x90 creative was accepted (salesagent-4764o, reproduced at the wire: the assignment
    was created with no error). Adding the parameters to the key would still have been the
    forbidden comparison, only narrower.

    ``adcp.canonical_formats.format_is_supported`` IS this rule and is called rather than
    reimplemented: it upgrades BOTH sides first -- which is the clause's "legacy named
    formats MUST be normalized to canonical declarations before comparison" -- then requires
    the claim to match every parameter the product fixed, while a parameter the product
    leaves open permits anything. All four of ``core/format-id.json``'s parameters, the
    density included: an omitted ``pixel_ratio`` on a sized reference is "the
    backward-compatible 1x variant", so a product fixing 2x refuses a claim that states 1x
    and one that states nothing, and a product fixing 1x accepts the silent one.

    ``identity.assertion`` is the input even though it is declared ``compare=False``. That
    flag means a reference is never compared BY RAW SPELLING -- the same agent respelled is
    the same agent -- and it is not a prohibition on this call, which is asked through
    ``format_resolver.format_accepted_by`` so both sides are canonically addressed first.
    Calling the SDK predicate directly was a defect: its own agent comparison applies four of
    the eight steps ``core/format-id.json`` makes a MUST, so a product naming the creative's
    agent with a trailing root-label dot or a removable dot segment refused that creative
    (salesagent-ol033.20.23). Comparing the key instead is what lost the parameters.

    NOT widened to compare the claim's KIND against the product's refs projected to kinds.
    The projection direction is legal, but ``formats.mdx`` says equal ``format_kind`` alone
    does not prove compatibility, and two legacy ids a buyer must keep apart
    (``display_300x250_image`` and ``display_728x90_image``) project to the same kind -- so
    that comparison would accept a creative the product does not take. The pin's unit is the
    DECLARATION, kind plus parameters, at which granularity those two separate cleanly.
    """
    from src.core.format_resolver import format_accepted_by

    claimed = identity.assertion
    if claimed is None and (option := identity.option) is not None:
        # AN OPTION REF NAMES A format_options[] ENTRY, and this product publishes none.
        # ``media-buy/package-request.json``: "Each reference matches one target product
        # ``format_options[]`` entry", and "an unresolved ``format_option_ref`` ... MUST be
        # rejected with ``UNSUPPORTED_FEATURE``" in step (1), before any co-present route is
        # compared. The deprecated arm is no substitute: it names legacy ids, not options.
        # This used to fall through to the ``claimed is None`` acceptance below, so an
        # option ref the product could not have published was accepted, alone or beside a
        # legacy id that did match. The SDK's projection mints a migrated option id for each
        # entry, but the product never published it, so it is no entry a ref can match.
        return _option_route(option, (), None)
    if claimed is None:
        if identity.kind is None or params is None:
            return _SATISFIED
        return _kind_route(identity.kind, [d for d in projected if d is not None], params)
    accepted = tuple(sorted(_reference_display(reference) for reference in legacy_references))
    accepting = [index for index, reference in enumerate(legacy_references) if format_accepted_by(claimed, reference)]
    if accepting:
        # Satisfied whether or not an accepting entry projected: the deprecated arm accepts
        # the reference. What it SELECTS is the projections, which a co-present route's
        # selection is compared with; an entry the SDK could not project contributes none.
        return FormatSatisfaction(
            satisfied=True,
            selection=tuple(declaration for index in accepting if (declaration := projected[index]) is not None),
        )
    # ``claimed`` is not None past the guard above, so the display takes the reference with no
    # narrowing cast and no fallback: a ``format_display(identity.legacy)`` branch here would be
    # unreachable, and the ``type: ignore`` it needed is what the ratchet refused.
    rejected = _reference_display(claimed)
    return FormatSatisfaction(
        satisfied=False,
        refusal=FormatRefusal.LEGACY_NOT_ACCEPTED,
        rejected_value=rejected,
        accepted_values=accepted,
        summary=(f"format {rejected!r} is not one this product accepts ({', '.join(accepted)})"),
        # The first entry naming the claim's template, as the canonical lane reports the first
        # candidate's shortfalls; none when no entry does.
        shortfalls=next(
            (found for reference in legacy_references if (found := _legacy_shortfalls(claimed, reference))), ()
        ),
    )


def _legacy_shortfalls(claimed: LibraryFormatId, supported: LibraryFormatId) -> tuple[ManifestShortfall, ...]:
    """Which parameters of *claimed* fail *supported*, when both name the same template.

    Empty when the two name different templates (agent and id, after both are canonically
    addressed and upgraded): that is a format the product does not take at all, not a
    creative of the right format at the wrong parameters.

    Each parameter is asked of the SDK on its own. The probe is *supported* with that one
    parameter replaced by the claim's, so ``format_accepted_by`` answers for exactly that
    field, with the SDK's own reading of an omitted ``pixel_ratio`` -- and an omitted
    ``pixel_ratio`` is reported as nothing stated, which is what the buyer sent. The one
    accepted value the product does not state is the density: core/format-id.json, "Omit for
    the backward-compatible 1x variant".
    """
    from src.core.format_resolver import canonically_addressed, format_accepted_by
    from src.core.schemas._base import _FORMAT_ID_PARAMETERS

    claim = upgrade_legacy_format_id(canonically_addressed(claimed))
    product = upgrade_legacy_format_id(canonically_addressed(supported))
    template = dict.fromkeys(_FORMAT_ID_PARAMETERS)
    if not formats_are_equivalent(claim.model_copy(update=template), product.model_copy(update=template)):
        return ()
    return tuple(
        ManifestShortfall(
            field=f"format_id.{name}",
            constraint=name,
            rejected_value=None if (stated := getattr(claim, name)) is None else _shown(stated),
            accepted_values=(_shown(1 if (fixed := getattr(product, name)) is None else fixed),),
        )
        for name in sorted(_FORMAT_ID_PARAMETERS)
        if not format_accepted_by(product.model_copy(update={name: getattr(claim, name)}), product)
    )


def _reference_display(reference: LibraryFormatId) -> str:
    """How a format REFERENCE is spelled to a buyer: the identity, plus what parameterizes it.

    ``format_display`` renders an identity KEY and so cannot show the parameters, which is
    precisely what a mis-sized refusal has to say -- "display_image is not one this product
    accepts (display_image)" names the same string twice and tells the buyer nothing. The
    parameter names come off the model, not a list, for the reason
    ``_FORMAT_ID_PARAMETERS`` exists.

    A buyer's reference whose ``agent_url`` the canonicalizer refuses has no identity to
    render, so it is shown as sent: the refusal it appears in is the reason it was refused.
    """
    from src.core.format_resolver import format_display
    from src.core.schemas._base import _FORMAT_ID_PARAMETERS, format_id_identity
    from src.core.signing.canonical import TargetUriMalformedError

    try:
        shown = format_display(format_id_identity(reference))
    except TargetUriMalformedError:
        shown = f"{reference.agent_url}/{reference.id}"
    parameters = sorted(
        (name, value) for name in _FORMAT_ID_PARAMETERS if (value := getattr(reference, name)) is not None
    )
    return f"{shown} ({', '.join(f'{name}={value}' for name, value in parameters)})" if parameters else shown


@dataclass(frozen=True, slots=True)
class PlacementLayer:
    """One INCLUDED placement and the seller-inline narrowing it states.

    ``core/product.json``'s ``format_options`` description computes a placement's
    effective accepted set as the intersection of "(1) the product format_options; (2) the
    product placement's inline format_options, when present; and (3) for kind
    publisher_ref, the named publisher's adagents.json catalog narrowing". This is layer
    (2). Layer (1) is the product's declarations, which the selected declaration already
    belongs to.

    ``declarations`` is None for a placement that states no narrowing of its own: "An omitted
    optional layer is unconstrained", so it admits every product declaration. It is still
    INCLUDED inventory, so a creative set routing every creative elsewhere leaves it uncovered.

    ``resolved`` is False for a placement whose layer (3) this seller cannot resolve: a
    ``publisher_ref`` placement, whose effective set depends on the publisher's adagents.json,
    for which this seller has no resolver on the buy path. The pin is explicit that such a
    layer is not "unconstrained": "an unresolved publisher placement or format-option
    reference MUST fail closed" (``core/product.json``).

    ``references`` is the placement's deprecated ``format_ids`` when it narrows through that
    arm alone, and empty otherwise. ``declarations`` is then their SDK projection, which is
    what a selector and a canonical creative are compared with; a creative asserting a legacy
    reference is compared with the references themselves on the deprecated lane, as the
    product's own arm compares it (:func:`creatives_uncovered`).

    A PUBLISHER SLICE is the same shape (:func:`publisher_slice_layers`): ``placement_id`` is
    the slice's ``publisher_domain`` and ``declarations`` the product declarations that apply
    to it.
    """

    placement_id: str
    declarations: tuple[LibraryFormatDeclaration, ...] | None
    resolved: bool = True
    references: tuple[LibraryFormatId, ...] = ()


def included_placement_layers(placements: Iterable[Any] | None) -> tuple[PlacementLayer, ...]:
    """The INCLUDED placements of a product and the narrowing each states, from stored documents.

    Only ``mode: included`` counts. ``core/placement.json`` makes ``included`` "fixed product
    inventory: it cannot be independently selected", so every package buys it, while a
    ``targetable`` placement is purchased only through ``placement_selection``, which this
    seller does not accept. *placements* are the stored documents, which ``get_products``
    serves unchanged and which state their ``mode`` (``ck_products_placements_explicit``),
    so the gate grades the mode the buyer was shown.

    A placement that states neither inline ``format_options`` nor the deprecated
    ``format_ids`` is an UNCONSTRAINED layer (``declarations`` None): "An omitted optional
    layer is unconstrained", so it accepts every product declaration. A placement whose
    declarations are all unparseable, or all unprojectable, keeps an EMPTY layer, which no
    selector and no canonical creative covers, so it fails closed rather than widening to the
    product set. A legacy creative is still compared with the references on the deprecated
    lane (``references``), as the product's own arm compares it.

    THE DEPRECATED ARM NARROWS TOO. ``core/placement.json`` ``format_ids``: "When present on a
    product placement, this field narrows the product-level ``format_ids`` contract and MUST
    NOT introduce formats the product does not accept." A placement stating only that arm --
    the shape a legacy-only product's placements have -- is read through the SDK's projection
    (:func:`_placement_projection`), the same one the product's own deprecated arm is graded
    through, so the two layers are compared in one vocabulary. The canonical arm wins when a
    placement states both, as ``core/product.json`` makes buyers "prefer ``format_options``".

    An included ``publisher_ref`` placement is kept as an UNRESOLVED layer (``resolved``
    False), whatever inline ``format_options`` it also states: layer (3) is that placement's
    adagents.json narrowing, which this seller has no resolver for on the buy path, and the
    pin says "an unresolved publisher placement or format-option reference MUST fail
    closed". So it refuses every package that selects a format until a resolver exists.
    Products this seller authors carry ``seller_inline`` placements, which are unaffected.
    """
    from src.core.format_resolver import product_format_references

    layers = []
    for placement in placements or []:
        if not isinstance(placement, Mapping) or placement.get("mode") != LibraryPlacementMode.included:
            continue
        placement_id = str(placement.get("placement_id"))
        if placement.get("kind") == LibraryPlacementKind.publisher_ref:
            layers.append(PlacementLayer(placement_id=placement_id, declarations=None, resolved=False))
        elif placement.get("format_options") is not None:
            layers.append(PlacementLayer(placement_id, product_declarations(placement["format_options"])))
        elif placement.get("format_ids") is not None:
            references = product_format_references(placement["format_ids"])
            layers.append(PlacementLayer(placement_id, _placement_projection(references), references=references))
        else:
            layers.append(PlacementLayer(placement_id, declarations=None))
    return tuple(layers)


def coverage_publishers(product: Any) -> list[dict[str, Any]] | None:
    """The ``publisher_properties`` a product's coverage is sliced by, or None when slicing changes nothing.

    Read off the product ORM row, and only when it can matter: the product stores no
    ``placements`` (they are the routing key whenever present) and one of its declarations
    is publisher-scoped. A product whose options are all product-local has every slice equal
    to its own set ("Product-local options apply to every slice"), so each package is
    covered by construction and the row's properties are not read.
    """
    if product.placements:
        return None
    if not any(d.publisher_domain for d in product_declarations(product.format_contract.format_options)):
        return None
    return list(product.effective_properties or [])


def publisher_slice_layers(
    publisher_properties: Iterable[Any] | None, declarations: Sequence[LibraryFormatDeclaration]
) -> tuple[PlacementLayer, ...]:
    """A multi-publisher product's eligibility slices, one layer per publisher, when it omits ``placements[]``.

    ``docs/creative/formats.mdx`` § Matching and satisfaction: "If a multi-publisher product
    omits ``placements[]``, each ``publisher_properties[]`` selector is an eligibility slice:
    product-local options apply to every slice, publisher-scoped options only to their
    publisher". ``canonical-formats.mdx`` states it again and adds that "distinct creatives
    must cover every slice". So each slice is a layer whose set is the product-local
    declarations plus the ones scoped to its ``publisher_domain``, and the coverage rule
    reads it exactly as it reads an included placement. Selectors naming one publisher
    are one slice: they share every declaration that applies.

    ``core/product.json`` puts the same case to the AUTHOR: such a product's
    ``format_options`` "MUST therefore be the common intersection accepted across every
    selected publisher/property scope". A product honouring that has every slice equal to its
    own set, and these layers then refuse nothing. A publisher-scoped option is not in the
    intersection, so a package selecting only it -- or a creative set satisfying only it --
    leaves another publisher's slice uncovered, which both texts refuse.

    A slice no declaration applies to is an EMPTY layer, which nothing covers, so it fails
    closed. A single-publisher product has one slice, its own set, and gets no layer.
    """
    domains = list(
        dict.fromkeys(
            str(selector["publisher_domain"])
            for selector in publisher_properties or []
            if isinstance(selector, Mapping) and selector.get("publisher_domain")
        )
    )
    if len(domains) < 2:
        return ()
    return tuple(
        PlacementLayer(domain, tuple(d for d in declarations if d.publisher_domain in (None, domain)))
        for domain in domains
    )


def outside_common_intersection(product: Any) -> tuple[LibraryFormatDeclaration, ...]:
    """The declarations of a product's served contract that not every publisher slice accepts.

    ``core/product.json`` ``format_options``, addressed to the product's AUTHOR: "If a product
    spans multiple publishers but omits placements[], there is no public routing key for
    per-placement creatives: its format_options MUST therefore be the common intersection
    accepted across every selected publisher/property scope. A seller that needs a union of
    publisher-specific formats MUST publish placements[] with publisher-scoped identities and
    narrowing." The slices are :func:`publisher_slice_layers`' -- the ones the buy path covers
    -- so the author is held to the set the buyer is graded against. Empty for a product with
    placements, a single publisher, or only product-local options.
    """
    publishers = coverage_publishers(product)
    if publishers is None:
        return ()
    declared = product_declarations(product.format_contract.format_options)
    slices = publisher_slice_layers(publishers, declared)
    return tuple(d for d in declared if any(d not in (layer.declarations or ()) for layer in slices))


def refuse_outside_common_intersection(product: Any) -> None:
    """``ValueError`` when a product's served contract is not the common intersection the pin requires of it.

    Asked of every product save -- the admin product forms, an inventory profile's save for
    each product it supplies the contract to, and :class:`ProductRepository`'s writes -- so
    no write path stores what :func:`outside_common_intersection` finds.
    """
    outside = outside_common_intersection(product)
    if outside:
        raise ValueError(
            f"Product {product.product_id!r} sells inventory of several publishers and publishes no "
            f"placements, so every format option must be accepted by all of them. Scoped to one "
            f"publisher: {', '.join(sorted({_declaration_label(d) for d in outside}))}. Declare "
            f"them product-local by omitting publisher_domain, or publish placements with "
            f"publisher-scoped identities."
        )


def _placement_projection(references: Iterable[LibraryFormatId]) -> tuple[LibraryFormatDeclaration, ...]:
    """A placement's deprecated ``format_ids``, projected by the SDK, as an id-less layer.

    Each entry goes through :func:`_projected_declaration`, which fails closed: an entry the
    SDK cannot project contributes nothing. The ``format_option_id`` the projection MINTS is
    removed, because it is no option this product published -- the SDK derives it from the
    reference's spelling, so ``display_300x250`` and ``display_image`` sized 300x250 mint
    different ids for one shape. Without an id the layer is matched to the product's
    declarations by ``core/product.json``'s third rule, "declarations with the same
    format_kind whose narrower parameters satisfy the broader declaration".
    """
    return tuple(
        projected.model_copy(update={"format_option_id": None})
        for reference in references
        if (projected := _projected_declaration(reference)) is not None
    )


def _narrowed(selected: LibraryFormatDeclaration, accepted: LibraryFormatDeclaration) -> LibraryFormatDeclaration:
    """*selected* with every parameter *accepted* STATES in its place: their intersection.

    ``canonical-formats.mdx`` § "Narrows": "when v2.params omits a parameter that v1
    specified, v2 inherits v1's value". So the narrower placement declaration keeps the
    product option's identity and every parameter it is silent about, and replaces the ones
    it states. STATED means set on the document, not filled by the per-kind schema's
    defaults: an inline declaration silent about ``slots`` inherits the option's slots, not
    the kind's default list.
    """
    params = accepted.params
    # One arm of the pinned union types ``params`` as a plain mapping rather than a model.
    if isinstance(params, BaseModel):
        stated = {name: value for name in params.model_fields_set if (value := getattr(params, name)) is not None}
    else:
        stated = {name: value for name, value in (params or {}).items() if value is not None}
    if not stated:
        return selected
    base = selected.params
    narrowed = base.model_copy(update=stated) if isinstance(base, BaseModel) else {**(base or {}), **stated}
    return selected.model_copy(update={"params": narrowed})


def _effective_declaration(
    selected: LibraryFormatDeclaration, accepted: LibraryFormatDeclaration
) -> LibraryFormatDeclaration | None:
    """What a placement's declaration *accepted* admits of the product declaration *selected*.

    None when the placement does not take *selected* at all; otherwise the NARROWER of the
    two, which is the pair's intersection and so the declaration a creative routed to this
    placement has to satisfy: *accepted* narrowed onto *selected* (:func:`_narrowed`) when
    the placement is the narrower side, *selected* itself when the option is.

    The matching rule is ``core/product.json``'s, stated for intersecting these layers:
    "Match publisher-declared options by {publisher_domain, format_option_id}, match
    product-local options by format_option_id when publisher_domain is omitted, and
    otherwise match declarations with the same format_kind whose narrower parameters
    satisfy the broader declaration." The last case is directional, and either side can be
    the narrower one, so both directions are asked:

    * FORWARD, the placement narrower: ``core/placement.json`` says its inline declarations
      "are intersected with the product-level format_options and MUST NOT introduce a
      format outside that product upper bound", so the placement's declaration is the claim
      and the selected product declaration is the target -- a placement narrowing
      ``[5000, 60000]`` to ``[5000, 30000]`` matches. Only the params the placement STATES
      are graded. ``canonical-formats.mdx``'s "narrows" definition: "when v2.params omits a
      parameter that v1 specified, v2 inherits v1's value", and the same document says "A
      placement without a locale policy inherits the matching product format option's
      policy". A placement restating the kind without a duration has inherited the
      product's, not failed to state it. One exception: a placement stating only a
      ``duration_ms_range`` still speaks to the option's ``duration_ms_exact``, so an exact
      duration outside that range does not match by inheritance.
    * REVERSE, the selected option narrower: the option is the claim and the placement's
      declaration the target, so a placement broader than the option it admits (a wider
      duration range, a larger file-size cap) matches, and an exact duration inside the
      placement's range matches through the cross-key rule of :func:`_unsatisfied_params`.

    A placement naming the option by id restates it, and whatever it states narrows it.
    """
    if (option := _option_identity(accepted)) is not None:
        return _narrowed(selected, accepted) if option == _option_identity(selected) else None
    if str(accepted.format_kind) != str(selected.format_kind):
        return None
    claim = {key: value for key, value in _declared_params(accepted).items() if value is not None}
    offered = {key: value for key, value in _declared_params(selected).items() if value is not None}
    inherited = _stated_constraints(offered, claim)
    if "duration_ms_range" in claim and "duration_ms_exact" in offered:
        inherited["duration_ms_exact"] = offered["duration_ms_exact"]
    if not _unsatisfied_params(claim, inherited):
        return _narrowed(selected, accepted)
    return selected if not _unsatisfied_params(offered, claim) else None


def _placement_sets(
    layers: Sequence[PlacementLayer], declarations: Sequence[LibraryFormatDeclaration]
) -> tuple[tuple[PlacementLayer, tuple[tuple[LibraryFormatDeclaration, LibraryFormatDeclaration], ...]], ...]:
    """Each RESOLVED layer with its effective set, as ``(product declaration, effective)`` pairs.

    ``core/product.json``: "A placement's effective accepted set is the intersection of every
    applicable layer". The pair keeps the product declaration beside what the placement admits
    of it, because the two questions asked of the set read different halves: a package selector
    covers a placement by SELECTING one of its product declarations, and a creative covers it by
    SATISFYING one of its effective declarations. An unconstrained layer admits each product
    declaration as it is. Unresolved layers are left out; see :func:`_unresolved_placement`.
    """
    return tuple(
        (
            layer,
            tuple((declaration, declaration) for declaration in declarations)
            if layer.declarations is None
            else tuple(
                (declaration, effective)
                for declaration in declarations
                for accepted in layer.declarations
                if (effective := _effective_declaration(declaration, accepted)) is not None
            ),
        )
        for layer in layers
        if layer.resolved
    )


def _unresolved_placement(layers: Sequence[PlacementLayer], field: str | None) -> FormatSatisfaction | None:
    """The refusal for the first included placement whose effective set cannot be computed.

    ``core/product.json``: "an unresolved publisher placement or format-option reference MUST
    fail closed" (:class:`PlacementLayer`). Reported before coverage on both paths, whatever
    was selected or assigned, because no selection can be shown to cover such a placement.
    """
    for layer in layers:
        if not layer.resolved:
            return FormatSatisfaction(
                satisfied=False,
                refusal=FormatRefusal.PLACEMENT_UNRESOLVED,
                rejected_value=layer.placement_id,
                summary=f"included placement {layer.placement_id!r} is publisher-referenced and unresolved",
                field=field,
            )
    return None


@dataclass(frozen=True, slots=True)
class _PlacementContract:
    """One placement's effective set, in the shape :func:`creative_satisfies` reads a contract in.

    The canonical arm only: an effective declaration is canonical whichever arm the product
    published, because the deprecated arm is compared through its projection.
    """

    format_options: tuple[LibraryFormatDeclaration, ...]
    format_ids: tuple[()] = ()


def _fills(
    creative: Any, layer: PlacementLayer, effective: _PlacementContract, contract: Any, *, legacy_lane: bool
) -> bool:
    """Whether *creative* satisfies the effective set of the included placement *layer*.

    Every branch asks :func:`creative_satisfies`, the one creative validator; they differ in
    the contract that stands for the placement's set.

    * An UNCONSTRAINED layer is the product's own contract. ``core/product.json``
      ``format_options``: "An omitted optional layer is unconstrained", so the intersection is
      layer (1) alone and the creative is asked exactly what the product gate asks it. Grading
      it against :class:`_PlacementContract` instead lost every deprecated entry the SDK
      cannot project -- an entry naming a tenant's own creative agent, or an unmapped id --
      and refused a creative the product accepts.
    * A layer narrowing through the deprecated ``format_ids`` on the DEPRECATED lane, asked of
      a creative asserting a legacy reference, is the product's contract AND the placement's
      references (``core/placement.json``: the field "narrows the product-level format_ids
      contract"), each compared as the product's own arm is compared (:func:`_legacy_lane`).
      The projection would again drop what the SDK cannot map.
    * Otherwise the placement's effective declarations, which a canonical creative's
      manifest is graded against. An empty set admits nothing, so it fails closed. A creative
      naming no option that fits one or more of them lies in the set even when the verdict is
      :attr:`FormatRefusal.MANIFEST_AMBIGUOUS`: the ref is required by what the PRODUCT
      publishes (``canonical-formats.mdx``: "the target product's ``format_options``"), which
      the product gate has already asked, and a placement narrowing one product option into
      several entries does not make the ref required.
    """
    from src.core.format_resolver import NamedFormats
    from src.core.schemas import CreativeFormatIdentity

    if layer.declarations is None:
        return bool(creative_satisfies(creative, contract))
    if legacy_lane and layer.references and CreativeFormatIdentity.from_row(creative).assertion is not None:
        return bool(creative_satisfies(creative, contract)) and bool(
            creative_satisfies(creative, NamedFormats(format_ids=layer.references))
        )
    if not effective.format_options:
        return False
    verdict = creative_satisfies(creative, effective)
    return bool(verdict) or verdict.refusal is FormatRefusal.MANIFEST_AMBIGUOUS


def creatives_uncovered(
    creatives: Sequence[tuple[Any, Collection[str] | None]], product: Any
) -> FormatSatisfaction | None:
    """The first included placement a package's creative set leaves uncovered, or None.

    ``core/product.json`` ``format_options``: "For a product or package containing multiple
    included placements, a single creative intended for every placement MUST lie in the
    intersection of every selected placement's effective set. Distinct per-placement
    creatives MAY use the union, but the selected creative set MUST cover every included
    placement; uncovered inventory MUST be rejected or refined, never silently omitted." This
    seller rejects. It is the creative-side twin of :attr:`SelectorResolution.uncovered`,
    over the same layers read the same way, and every creative gate asks it of the package's
    WHOLE creative set once the request's assignments are in place.

    *creatives* is that set: each stored creative row with the ``placement_ids`` its
    assignment routes it to, None for an assignment that names none ("When omitted, the
    creative is eligible for all placements", ``sync-creatives-request.json``). *product* is
    the product ORM row: its ``format_contract`` and its stored ``placements`` documents, the
    two things the create-time selector gate reads off the same row.

    A creative covers a placement when it is routed there and satisfies the placement's
    effective set, asked of :func:`creative_satisfies` -- the one creative validator, manifest
    included -- against the contract that stands for that set (:func:`_fills`). Graded against the EFFECTIVE
    declaration rather than the product declaration the creative selected, because a product
    option may be broader than the placement: a 300x250 creative satisfies an unsized image
    option and still does not fill a placement narrowing it to 728x90.

    An empty set covers nothing and is not refused: a package may be bought before its
    creatives are assigned. A product declaring neither arm imposes no format restriction, so
    it has no effective set to grade against (:func:`resolve_routes`).

    A MULTI-PUBLISHER PRODUCT WITH NO ``placements[]`` is covered slice by slice
    (:func:`publisher_slice_layers`): ``canonical-formats.mdx``, "distinct creatives must
    cover every slice". A creative that satisfies only one publisher's scoped option does not
    fill another publisher's slice, and the refusal names that slice's ``publisher_domain``.
    """
    if not creatives:
        return None
    # The product's upper bound and included placements, read exactly as a package selector
    # reads them -- which is what makes this the selector rule's twin rather than a second
    # reading of the product.
    contract = product.format_contract
    bound = resolve_routes(
        (),
        contract.format_options,
        legacy_catalog=contract.format_ids,
        placements=product.placements,
        publishers=coverage_publishers(product),
    )
    unresolved = _unresolved_placement(bound.placements, field=None)
    if unresolved is not None:
        return unresolved
    for layer, pairs in _placement_sets(bound.placements, bound.declarations):
        effective = _PlacementContract(tuple(admitted for _, admitted in pairs))
        if any(
            (routed is None or layer.placement_id in routed)
            and _fills(creative, layer, effective, contract, legacy_lane=not bound.published)
            for creative, routed in creatives
        ):
            continue
        return FormatSatisfaction(
            satisfied=False,
            refusal=FormatRefusal.PLACEMENT_UNCOVERED,
            # The placement the set leaves unserved: with several creatives there is no single
            # offending creative, and the placement is what the buyer has to fill.
            rejected_value=layer.placement_id,
            # What a creative must satisfy to cover it, named by the option ids the buyer was
            # shown. A legacy-only product published none, and the ids its projection mints
            # would name options the buyer never saw, so it names nothing.
            accepted_values=tuple(sorted({_declaration_label(e) for e in effective.format_options}))
            if bound.published
            else (),
            summary=f"the package's creatives do not cover included placement {layer.placement_id!r}",
        )
    return None


def _same_selection(
    left: Sequence[LibraryFormatDeclaration | None], right: Sequence[LibraryFormatDeclaration | None]
) -> bool:
    """Whether two arms selected the same option SET, order and repetition aside.

    Membership by ``==`` rather than a ``set``: the pinned declaration models are not
    hashable, and ``==`` compares their field values, which is what "the same product
    option set" means. ``None`` -- a route that selected nothing -- equals only ``None``.
    """
    return all(d in right for d in left) and all(d in left for d in right)


def selector_routes(
    *,
    format_kind: str | None,
    format_option_refs: Sequence[LibraryFormatOptionReference] | None,
    params: Mapping[str, Any] | None,
    format_ids: Sequence[LibraryFormatId] | None,
    catalogue: Sequence[Format],
) -> tuple[FormatRoute, ...]:
    """Every route a PACKAGE's format selector names, as separate routes.

    ``media-buy/package-request.json`` gives a package three independent ways to name a
    format and permits several at once. ONE ROUTE PER ARM, not one merged claim: the pin
    requires co-present routes to be resolved INDEPENDENTLY (steps 11 and 12) and says
    outright that "canonical precedence MUST NOT hide the dimensional conflict" (step 13),
    which is only possible if the legacy route is still resolved after a canonical one has
    succeeded. A merged claim can answer yes/no once; it cannot answer "these two routes
    named different declarations".

    An earlier version of this DID merge them -- it took the first of each arm and folded
    them into one identity -- and the consequence was step 15 answering success: a package
    carrying a resolving option ref beside an UNPROJECTABLE legacy id was accepted on the
    option route, and the legacy route was never resolved at all.

    Order matters and is the pin's -- step (4), "apply precedence: ``format_option_refs``,
    then ``format_kind`` plus ``params``, then ``format_ids``". So the canonical arms come
    first and the deprecated one last, and that ordering does two jobs in
    :class:`SelectorResolution`: it decides which refusal a buyer sees when more than one
    route is broken, and it names the WINNING route whose declaration the package is
    accepted on.

    The kind a legacy reference projects to is asked of ``format_resolver.canonical_kind_for``
    -- the one implementation of the one direction that resolves -- and an unresolvable
    reference leaves ``kind`` as ``None`` rather than guessing, exactly as
    ``creative_identity_of`` does for a creative. The legacy route is then refused against a
    canonical product (``LEGACY_UNPROJECTABLE``) instead of read as "no claim".

    WITH THE SERVED DECLARATION, read from *catalogue* (``seller_format_catalogue``), as the
    creative write reads it. Without it the registry's step 2 -- the v1 format's own
    ``canonical`` -- and step 4 -- its structural shape -- had nothing to read on this route,
    so 42 of the 71 formats the reference agent serves (``audio_standard_30s`` among them)
    were refused LEGACY_UNPROJECTABLE on a package while ``sync_creatives`` accepted the same
    id (salesagent-ol033.20.24). One catalogue lookup, one projection: the two paths cannot
    answer differently about one reference.
    """
    from src.core.format_resolver import canonical_kind_for, find_format

    routes: list[FormatRoute] = []
    # Distinct loop names for the two arms, deliberately: one ``reference`` reused across
    # both narrowed to the option-ref type and mypy then rejected the legacy arm's three
    # uses of it. The arms carry different pinned types and the names say which.
    for option_ref in format_option_refs or []:
        routes.append(
            FormatRoute(
                FormatSelector(option=FormatOptionIdentity.of_reference(option_ref)),
                field="format_option_refs",
            )
        )
    if format_kind is not None:
        # The ONE route ``params`` belongs to: a buyer's params accompany ``format_kind``.
        routes.append(FormatRoute(FormatSelector(kind=format_kind), params=params or {}, field="format_kind"))
    for legacy_ref in format_ids or []:
        kind: str | None = None
        try:
            kind = canonical_kind_for(legacy_ref, spec=find_format(catalogue, legacy_ref))
        except ValueError:
            logger.debug(
                "No canonical projection for package selector %r; the reference is its only identity", legacy_ref
            )
        # ``params={}`` and not ``None``: a package's legacy reference STATES its params, and
        # they are exactly the ones it carries or its normalization projects. ``None`` is a
        # stored creative's "params unknown" and skips the grading -- which, for a reference
        # that normalizes to a kind and nothing else (a template such as ``display_image``
        # with no size, or an id the registry maps by glob but the SDK does not upgrade),
        # accepted it against a product fixing 300x250. core/product-format-declaration.json:
        # "A broad request with no dimensions or duration does not satisfy a fixed-size or
        # fixed-duration product". A raw-key gate after satisfaction used to refuse that by
        # accident; it is gone (salesagent-ol033.20.26), so the rule is applied here instead.
        routes.append(FormatRoute(FormatSelector.of_legacy(legacy_ref, kind=kind), params={}, field="format_ids"))
    return tuple(routes)


@dataclass(frozen=True, slots=True)
class SelectorResolution:
    """Every route a selector named, resolved, and the four answers the pin derives.

    ONE stored field -- the (route, verdict) pairs -- and every answer a PROPERTY over
    them, because the pin states its steps as an ORDER and an order written down twice is
    an order two readers can disagree about. ``media-buy/package-request.json``'s
    "Format selector contract (normative)" is the whole of this class:

      (1) independently resolve each present route to canonical declarations, without
          applying route precedence; an unresolved ``format_option_ref`` or a legacy
          ``format_id`` that cannot be normalized through the canonical mapping path MUST
          be rejected with ``UNSUPPORTED_FEATURE``;
      (2) when every route resolves, derive the product ``format_options[]`` entries
          selected by each route using directional product satisfaction, and require the
          selected option sets to match;
      (3) reject different format shapes, selected option sets, or incompatible dimensions
          with ``CONFLICTING_SELECTORS``; and only then
      (4) apply precedence: ``format_option_refs``, then ``format_kind`` plus ``params``,
          then ``format_ids``.

    plus the single-route rule stated beside them: "A single resolved route that does not
    satisfy the product is rejected with ``UNSUPPORTED_FEATURE``."

    THE ROUTES ARE A TUPLE OF PAIRS rather than of verdicts because the refusal's details
    name the route the buyer SUPPLIED as well as what it normalized to
    (:class:`SelectorConflictDetails`), and a verdict does not carry its own claim.
    """

    #: One pair per arm the buyer named, in step (4)'s precedence order. Empty where the
    #: product narrows neither arm, or the package names no selector: both mean the
    #: selector imposes nothing, which is :meth:`__bool__`'s ``True`` with no selection --
    #: unless the product has a placement or slice none of its declarations covers
    #: (:attr:`uncovered`).
    routes: tuple[tuple[FormatRoute, FormatSatisfaction], ...] = ()
    #: The product's included placements and their narrowing, for :attr:`uncovered`.
    #: Empty for every single-route caller (a stored creative), which is what keeps the
    #: coverage rule on the package path only.
    placements: tuple[PlacementLayer, ...] = dataclass_field(default=(), compare=False)
    #: The product's canonical declarations, for :attr:`uncovered`'s ``accepted_values``:
    #: the ones every included placement admits.
    declarations: tuple[LibraryFormatDeclaration, ...] = dataclass_field(default=(), compare=False)
    #: Whether those declarations are ones the PRODUCT published. False on the deprecated
    #: lane, where they are the SDK's projection of its ``format_ids``: the routes are resolved
    #: and compared against the projection, but the product published no declaration, so an
    #: accepted package records none (:attr:`selection`) -- a snapshot of a projection would
    #: name an option id the buyer was never shown.
    published: bool = dataclass_field(default=True, compare=False)

    @property
    def unresolved(self) -> FormatSatisfaction | None:
        """Step (1): the first route that never became a canonical declaration.

        REPORTED BEFORE EVERYTHING ELSE, and the pin states that as an obligation rather
        than an implementation choice -- the storyboard's
        ``reject_unprojectable_legacy_dual_emission`` says "Resolution failure is reported
        BEFORE selector equivalence and must not be mislabeled ``CONFLICTING_SELECTORS``".
        A seller that compared the routes which DID resolve and answered
        ``CONFLICTING_SELECTORS`` would emit a plausible-looking refusal with the wrong
        code, which a scenario asserting only on success could never catch.
        """
        return next((verdict for _, verdict in self.routes if verdict.refusal in _RESOLUTION_FAILURES), None)

    @property
    def _arms(self) -> tuple[tuple[tuple[FormatRoute, FormatSatisfaction], ...], ...]:
        """The (route, verdict) pairs grouped by ARM -- one request field each -- winner first.

        THE PIN'S ROUTE IS THE ARM. ``media-buy/package-request.json`` names three selector
        routes -- "format_option_refs, or format_kind with optional params", and the
        deprecated format_ids -- and an array arm is ONE route selecting an option SET:
        ``format_option_refs`` "Each reference matches one target product ``format_options[]``
        entry", and the receiver must "require each route to select the same product option
        set". :func:`selector_routes` still builds one :class:`FormatRoute` per array entry,
        because step (1) resolves each reference and refuses an unresolved one "with a field
        path to the failing entry"; this is where they are put back together for steps (2)
        and (3), and for the refusal's per-route summary.

        Grouped in :attr:`routes` order, which :func:`selector_routes` builds arm by arm in
        step (4)'s precedence, so the first group is the winning arm's.
        """
        arms: dict[str | None, list[tuple[FormatRoute, FormatSatisfaction]]] = {}
        for route, verdict in self.routes:
            arms.setdefault(route.field, []).append((route, verdict))
        return tuple(tuple(pairs) for pairs in arms.values())

    @property
    def _arm_selections(self) -> tuple[tuple[LibraryFormatDeclaration | None, ...], ...]:
        """The declarations each ARM selected, one tuple per request field, winner first.

        Two refs on one arm are one selection of two options, which is how a buyer covers two
        included placements that accept different formats (``core/product.json``: "Distinct
        per-placement creatives MAY use the union, but the selected creative set MUST cover
        every included placement").

        An entry contributes EVERY declaration it selected (:func:`_selecting`: a broad legacy
        id or a bare kind selects each declaration it is compatible with), and an entry that
        selected nothing contributes ``None``, which matches no declaration and so still
        differs from any arm that selected something.
        """
        return tuple(
            tuple(declaration for _, verdict in pairs for declaration in (verdict.selection or (None,)))
            for pairs in self._arms
        )

    @property
    def _winning_arm(self) -> tuple[LibraryFormatDeclaration, ...]:
        """The declarations the first arm selected, once each, whether or not it is accepted.

        Once each because two refs naming one option select one entry, and a package records
        one snapshot per selected entry.
        """
        chosen: list[LibraryFormatDeclaration] = []
        for declaration in self._arm_selections[0] if self.routes else ():
            if declaration is not None and declaration not in chosen:
                chosen.append(declaration)
        return tuple(chosen)

    @property
    def conflicting(self) -> bool:
        """Steps (2) and (3): every route resolved, and the selected option sets disagree.

        The comparison is on the SELECTED SETS, per arm, which is what step (2) asks for --
        "derive the product ``format_options[]`` entries selected by each route ... and
        require the selected option sets to match" -- and not on the routes' own shapes. Two
        routes spelled differently that select the same entries agree; that is the
        storyboard's ``create_media_buy_with_equivalent_dual_emission``, where a
        product-local option ref and ``display_300x250_image`` "normalize to the same
        declaration". Two arms whose sets merely OVERLAP do not: a legacy id two options both
        narrow selects both, and an option ref beside it selects one, which
        ``format_option_refs`` answers directly -- "require each route to select the same
        product option set". Entries of ONE arm never conflict with each other: together
        they are that arm's set (:attr:`_arm_selections`).

        A ROUTE THAT SELECTED NOTHING COUNTS AS A DIFFERING SET, and that is the pin's
        reading rather than a choice. ``reject_conflicting_canonical_routes`` sends a
        resolving option ref beside ``{format_kind: image, params: {width: 728, height:
        90}}`` to a product publishing ONE image option fixed at 300x250, and grades
        ``CONFLICTING_SELECTORS`` -- the 728x90 route can select no entry, because there is
        none to select, so there is no other mechanism by which that step produces that
        code on that fixture.

        Compared as SETS by :func:`_same_selection`, with ``==`` on the pinned declaration
        models, which compares their field VALUES; order and repetition within an arm do not
        matter, and ``None`` (a route that selected nothing) equals only ``None``.
        """
        arms = self._arm_selections
        if self.unresolved is not None or len(arms) < 2:
            return False
        first = arms[0]
        return any(not _same_selection(arm, first) for arm in arms[1:])

    @property
    def unsatisfied(self) -> FormatSatisfaction | None:
        """The single-route rule: a route that resolved and selected nothing.

        Reached only when nothing failed resolution and no route disagrees with another.
        With one route that is the pin's "a single resolved route that does not satisfy the
        product"; with several it is every route agreeing on the empty set, which no
        comparison can fault and which still satisfies no product declaration.
        """
        if self.unresolved is not None or self.conflicting:
            return None
        return next((verdict for _, verdict in self.routes if not verdict), None)

    @property
    def uncovered(self) -> FormatSatisfaction | None:
        """The first included placement the selected declaration set does not cover, or None.

        ``docs/creative/formats.mdx`` § Matching and satisfaction: "For a product with
        multiple included placements, the selected format set must cover every placement. A
        seller must reject or refine a package with an uncovered placement; it must not
        silently remove inventory." This seller rejects. ``core/product.json`` restates it:
        "uncovered inventory MUST be rejected or refined, never silently omitted".

        Asked only once every route resolved, agreed and selected a declaration, because
        coverage is a property of the SELECTED set. A product with one included placement is
        held to the same rule: that placement is all of the package's inventory.

        A PACKAGE NAMING NO SELECTOR "selects all formats supported by the product"
        (``media-buy/package-request.json``), so its selected set is every product
        declaration, graded by the same rule. That covers every placement and slice some
        declaration applies to, and refuses one none does: an empty publisher slice
        (:func:`publisher_slice_layers`), whose only cure is another product. The refusal
        names the uncovered unit at ``product_id``, because the buyer selected nothing to
        name.

        The SET is the winning arm's, because the pin states the rule over a set: each
        included placement must accept at least one declaration the arm selected. Two refs
        selecting the two options of a product whose placements accept one each cover both.
        A multi-publisher product with no placements is held to it slice by slice
        (:func:`publisher_slice_layers`): a selection of one publisher's scoped options
        leaves every other publisher's slice uncovered.

        Read off :attr:`_winning_arm` and never through :attr:`selection`, which asks
        ``bool(self)`` and so this property: the winning arm is the first, as there.

        AN UNRESOLVED PLACEMENT is reported first, whatever a selector selected -- and when
        the package selected nothing, at ``product_id``: a ``publisher_ref`` placement's
        effective set cannot be computed here, so no selection, the product's whole set
        included, can be shown to cover it, and ``core/product.json`` says "an unresolved
        publisher placement or format-option reference MUST fail closed"
        (:class:`PlacementLayer`).
        """
        if not self.placements:
            return None
        if self.unresolved is not None or self.conflicting or self.unsatisfied is not None:
            return None
        field = self.routes[0][0].field if self.routes else "product_id"
        unresolved = _unresolved_placement(self.placements, field)
        if unresolved is not None:
            return unresolved
        if self.routes:
            chosen = self._winning_arm
            # The selection as the buyer can recognise it: the option ids a product published,
            # or on the deprecated lane -- whose declarations are the SDK's projection, carrying
            # ids the projection minted and the buyer was never shown -- the references sent.
            labels = (
                sorted(_declaration_label(d) for d in chosen)
                if self.published
                else sorted({_route_selector(route) for route, _ in self._arms[0]})
            )
        else:
            chosen, labels = self.declarations, []
        if not chosen:
            return None
        sets = _placement_sets(self.placements, self.declarations)
        for layer, pairs in sets:
            if not any(declaration in chosen for declaration, _ in pairs):
                return FormatSatisfaction(
                    satisfied=False,
                    refusal=FormatRefusal.PLACEMENT_UNCOVERED,
                    # One value when the arm selected one; a set has no single offending
                    # value, and the field pointer already names the arm. With no selector
                    # the buyer supplied nothing, and the unit left unserved is the value,
                    # as on the creative-set refusal (:func:`creatives_uncovered`).
                    rejected_value=(labels[0] if len(labels) == 1 else None) if self.routes else layer.placement_id,
                    # What this product would accept from this package on its own: the
                    # declarations every included placement admits. Empty when the
                    # placements share none, which is when only a set can cover them.
                    # Nothing on the deprecated lane, for the reason ``labels`` gives.
                    accepted_values=tuple(
                        sorted(
                            _declaration_label(d)
                            for d in self.declarations
                            if all(any(admitted == d for admitted, _ in each) for _, each in sets)
                        )
                    )
                    if self.published
                    else (),
                    summary=f"selected formats {labels or '(all of the product)'!r} do not cover {layer.placement_id!r}",
                    field=field,
                )
        return None

    @property
    def refused(self) -> FormatSatisfaction | None:
        """The verdict to raise ``UNSUPPORTED_FEATURE`` for, or None. Every lane, in order.

        ``is not None`` and NEVER ``self.unresolved or self.unsatisfied``, because a
        refusing :class:`FormatSatisfaction` is FALSY by design -- its ``__bool__`` is
        "did it satisfy". The ``or`` spelling therefore discards exactly the verdict this
        property exists to return and falls through to the second lane, which for a single
        unresolved route is ``None``: measured before the fix, an unpublished
        ``format_option_ref`` and a package carrying an unprojectable legacy id beside a
        resolving option ref were both ACCEPTED.
        """
        unresolved = self.unresolved
        if unresolved is not None:
            return unresolved
        unsatisfied = self.unsatisfied
        return unsatisfied if unsatisfied is not None else self.uncovered

    @property
    def selection(self) -> tuple[LibraryFormatDeclaration, ...]:
        """Step (4): the declarations the WINNING arm selected, once the selector is accepted.

        The first arm is the winner because :func:`selector_routes` builds the routes in the
        pin's precedence order. A set, because an array arm selects one entry per reference
        (:attr:`_arm_selections`). Empty where the selector imposes nothing -- a package
        naming no format, or a product that narrows neither arm -- which is a real answer
        and not a missing one: there is no canonical declaration to record for such a
        package. Empty too where the declarations are the projection of a legacy-only
        product's deprecated arm (:attr:`published`).
        """
        return self._winning_arm if bool(self) and self.published else ()

    def __bool__(self) -> bool:
        return self.refused is None and not self.conflicting


def resolve_routes(
    routes: Sequence[FormatRoute],
    declarations: Any,
    *,
    legacy_catalog: Iterable[Any] | None = None,
    placements: Iterable[Any] | None = None,
    publishers: Iterable[Any] | None = None,
) -> SelectorResolution:
    """Resolve EVERY route independently. :class:`SelectorResolution` carries the verdict.

    Independently, and that word is the pin's: "without applying route precedence". An
    earlier version resolved until the first route succeeded, and the consequence was the
    storyboard's step 15 answering success -- a package carrying a resolving option ref
    beside an UNPROJECTABLE legacy id was accepted on the option route and the legacy
    route was never resolved at all.

    *placements* is the product's stored ``placements`` (served unchanged), for the coverage rule
    (:attr:`SelectorResolution.uncovered`). Read on both lanes: on the deprecated one the
    selected set is the SDK's projection of the deprecated arm (:func:`_legacy_lane`), and an
    included ``publisher_ref`` placement fails closed whichever arm the product publishes.
    *publishers* is the product's ``publisher_properties`` (:func:`coverage_publishers`), read
    only when it stores no placements: each publisher is then an eligibility slice
    (:func:`publisher_slice_layers`) covered by the same rule.

    A product publishing ``format_options[]`` is graded on them; one publishing only the
    deprecated ``format_ids`` is graded on that arm, with each entry also projected to its
    canonical declaration so routes of different arms can be compared
    (:func:`_projected_declaration`).
    """
    from src.core.format_resolver import product_format_references

    declared = product_declarations(declarations)
    legacy_references = product_format_references(legacy_catalog)
    if not declared and not legacy_references:
        # The product declares neither arm, so it imposes no restriction -- the reading all
        # four replaced gates already applied to an empty ``format_ids``. No route is
        # resolved, which is also what makes ``selection`` empty: there is no declaration.
        return SelectorResolution()

    projected = () if declared else tuple(_projected_declaration(reference) for reference in legacy_references)
    # ``routes`` may be empty -- a package naming no format at all. Which arm it SHOULD
    # have named is the product's business, not this function's: ``core/product.json`` does
    # not require a package to select a format, and the pre-existing legacy branch handles
    # an absent selector by deriving the ids from the product.
    return SelectorResolution(
        tuple(
            (
                route,
                replace(
                    _canonical_lane(route.claim, declared, route.params, route.manifest)
                    if declared
                    else _legacy_lane(route.claim, legacy_references, projected, route.params),
                    field=route.field,
                ),
            )
            for route in routes
        ),
        placements=included_placement_layers(placements)
        if placements
        else publisher_slice_layers(publishers, declared),
        declarations=declared or tuple(declaration for declaration in projected if declaration is not None),
        published=bool(declared),
    )


def satisfies(
    identity: FormatSelector,
    declarations: Any,
    *,
    params: Mapping[str, Any] | None = None,
    legacy_catalog: Iterable[Any] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> FormatSatisfaction:
    """Whether the format *identity* names satisfies what a product declares.

    Args:
        identity: the claim -- a stored creative's identity
            (:meth:`CreativeFormatIdentity.from_row`) or a package's format selector. The
            arms are not interchangeable and are tried in the pin's precedence: an exact
            option ref routes to one declaration, a legacy assertion projects, and a bare
            kind only discriminates.
        declarations: the product's canonical arm -- ``format_options``, as the stored
            documents or as converted models. Both arrive; :func:`product_declarations`
            absorbs the difference.
        params: the canonical narrowing the claim carries, and the ABSENT/EMPTY distinction
            is load-bearing. ``None`` means the claim does not state its params, which is
            true of every stored creative (the columns are ``format_kind`` and
            ``format_option_ref``; a creative's dimensions live on its assets) -- the params
            rules are skipped. A MAPPING, including ``{}``, means the claim states these and
            no others, which is true of a package selector: a buyer who sends
            ``format_kind`` and no ``params`` has claimed no dimensions, and that claim is
            graded. Collapsing the two would make
            ``reject_bare_image_selector_for_fixed_mrec`` and
            ``create_media_buy_with_canonical_format_option`` the same input.
        legacy_catalog: the product's deprecated arm -- ``format_ids``, as stored. Read
            through ``format_resolver.product_format_identities``, so one corrupt entry is
            dropped rather than raised.
        manifest: a canonical creative's assets (:attr:`FormatRoute.manifest`), graded
            against the declaration the route selects. :func:`creative_satisfies` is the
            caller that supplies it.

    Returns:
        A :class:`FormatSatisfaction`. Satisfied when the product declares NEITHER arm,
        which is the reading all four replaced gates already applied to an empty
        ``format_ids``: a product that has not narrowed itself imposes no restriction.

    THE SINGLE-ROUTE ENTRY, for a claim that addresses a format exactly one way -- which is
    every stored creative, because its identity is columns rather than a request's arms.
    A package selector may name several and goes through :func:`resolve_routes`.

    The satisfied verdict carries the declaration the one route selected, so a caller that
    needs the selection rather than the verdict does not have to ask twice.
    """
    resolution = resolve_routes(
        (FormatRoute(identity, params, manifest=manifest),), declarations, legacy_catalog=legacy_catalog
    )
    # Unreachable by construction: ``conflicting`` needs two routes to disagree, and there
    # is one. Asserted rather than assumed because this is the one claim the projection
    # below makes -- a conflict reported as "satisfied" would be a silent acceptance.
    assert not resolution.conflicting, "a single route cannot conflict with itself"
    # ``is not None``, never ``or``: a refusing verdict is FALSY, so the ``or`` spelling
    # would discard every refusal and answer satisfied. See :attr:`SelectorResolution.refused`.
    refused = resolution.refused
    if refused is not None:
        return refused
    return FormatSatisfaction(satisfied=True, selection=resolution.selection)


def accepts_legacy_reference(
    reference: LibraryFormatId,
    declarations: Any,
    legacy_catalog: Iterable[Any] | None,
    *,
    catalogue: Sequence[Format],
) -> bool:
    """Whether a product would accept a package naming *reference* as its only format selector.

    The deprecated ``format_ids`` filter of ``get_products`` asks this of every product.
    docs/creative/template-format-ids.mdx § Compatibility (v3.2.7): legacy ids are
    normalized "to canonical declarations immediately and do all matching on the canonical
    shape", so the filter is answered by the same normalization and satisfaction the package
    route runs (:func:`selector_routes`, :func:`resolve_routes`) and never by comparing raw
    ``(agent_url, id)`` pairs. A product that declares neither format arm imposes no
    restriction on a package, but it does not ACCEPT any named format, so it does not
    match a filter that names one.
    """
    from src.core.format_resolver import product_format_references

    if not product_declarations(declarations) and not product_format_references(legacy_catalog):
        return False
    routes = selector_routes(
        format_kind=None, format_option_refs=None, params=None, format_ids=[reference], catalogue=catalogue
    )
    return resolve_routes(routes, declarations, legacy_catalog=legacy_catalog).refused is None


def creative_satisfies(creative: Any, contract: Any) -> FormatSatisfaction:
    """Whether a STORED creative satisfies a product's format contract. Every creative gate asks here.

    THE ONE VALIDATOR for a creative against a product, and four gates ask it: the two on the
    create path, the update path and the ``sync_creatives`` assignment path. Each used to
    build the identity and call :func:`satisfies` itself with no manifest, so a canonical
    creative was graded on its kind alone and a same-kind 728x90 creative satisfied a product
    fixing 300x250 -- the fifth rule of ``formats.mdx``, "Equal format_kind values alone do
    not prove compatibility", broken on the creative side.

    *creative* is the ORM row: the identity is read through
    :meth:`CreativeFormatIdentity.from_row` and the manifest is the row's stored ``assets``.
    *contract* is the product's ``format_contract``. The manifest travels only for a
    canonical creative -- one asserting no legacy reference -- for the reason the module
    docstring gives.

    A CANONICAL MANIFEST IS ALWAYS GRADED. When the product selects no declaration for it --
    it publishes only the deprecated ``format_ids``, or neither arm -- the verdict is
    satisfied with an empty selection, and the manifest is then held to its kind
    (:func:`kind_manifest_verdict`), the same rule a library sync applies when nothing is
    assigned. Otherwise a manifest missing its kind's required slot was graded nowhere.

    A LEGACY CREATIVE CARRYING AN OPTION REF is asked twice, by :func:`_legacy_with_option`.
    """
    from src.core.schemas import CreativeFormatIdentity

    identity = CreativeFormatIdentity.from_row(creative)
    selector = identity.as_selector()
    if identity.assertion is not None:
        return _legacy_with_option(selector, contract) if identity.option is not None else _asked(selector, contract)
    assets = (creative.data or {}).get("assets")
    verdict = _asked(selector, contract, manifest_document(assets))
    if verdict and not verdict.selection:
        return kind_manifest_verdict(identity.kind, assets)
    return verdict


def _asked(selector: FormatSelector, contract: Any, manifest: Mapping[str, Any] | None = None) -> FormatSatisfaction:
    """:func:`satisfies` over a product's ``format_contract``, both arms."""
    return satisfies(selector, contract.format_options, legacy_catalog=contract.format_ids, manifest=manifest)


def _legacy_with_option(selector: FormatSelector, contract: Any) -> FormatSatisfaction:
    """A creative naming a legacy ``format_id`` AND a ``format_option_ref``: both must hold.

    ``core/creative-asset.json`` puts ``format_option_ref`` beside the identifier, so a v1
    item may carry one, and the ref then names the declaration -- but it does not replace
    what the ``format_id`` says. ``formats.mdx``: "Equal ``format_kind`` values alone do not
    prove compatibility", and creative-manifests.mdx § Validation step 5: "do not silently
    relax the target contract". Routed by the ref alone, a ``display_728x90_image`` creative
    naming a 300x250 option was accepted onto a product fixing 300x250, because the option
    route never graded the reference and a legacy creative carries no manifest to grade.

    So each half is resolved ON ITS OWN -- the ref without the reference, and the reference
    without the ref, through the same routes as any claim -- and the creative is accepted
    only when the declaration the ref selects is one the reference selects too. A half that
    refuses is that refusal; two halves that each resolve but disagree are
    :attr:`FormatRefusal.LEGACY_NOT_OPTION`. Both are ``UNSUPPORTED_FEATURE`` at the raise
    site (:func:`canonical_refusal`), the code ``media-buy/package-request.json`` gives "a
    single resolved route that does not satisfy" the product. ``CONFLICTING_SELECTORS`` is
    not the code: enums/error-code.json defines it for "A 3.x package request" naming several
    routes, and this is a creative.
    """
    by_option = _asked(replace(selector, legacy=None, legacy_parameters=(), assertion=None), contract)
    if not by_option:
        return by_option
    by_reference = _asked(replace(selector, option=None), contract)
    if not by_reference or by_option.declaration is None or by_option.declaration in by_reference.selection:
        return by_reference if not by_reference else by_option
    assert selector.assertion is not None, "a legacy creative asserts its reference"
    return FormatSatisfaction(
        satisfied=False,
        refusal=FormatRefusal.LEGACY_NOT_OPTION,
        rejected_value=selector.assertion.id,
        accepted_values=(_declaration_label(by_option.declaration),),
        summary=(
            f"named format {selector.assertion.id!r} does not select the declaration "
            f"{_declaration_label(by_option.declaration)!r} its format_option_ref names"
        ),
    )


def canonical_refusal(
    verdict: FormatSatisfaction,
    *,
    field: str | None = None,
    **entity: Any,
) -> AdCPCapabilityNotSupportedError:
    """The ``UNSUPPORTED_FEATURE`` to raise for a CANONICAL refusal. One shape, five sites.

    Here rather than at each raise site because the shape is identical at all of them and
    the pin fixes it: ``core/error.json`` names ``rejected_value`` + ``accepted_values`` as
    the canonical rejection-set keys "rather than seller-specific variants observed in the
    wild", and the SDK's own ``FormatKindNotInClosedSetError.to_wire_error`` builds exactly
    that pair for exactly this condition. Five hand-written copies of it would be five
    chances to pick a different key, which is the defect ``CapabilityRefusalDetails`` was
    introduced to end.

    :attr:`FormatRefusal.LEGACY_NOT_ACCEPTED` reaches this from ONE site: the package
    selector gate, where ``media-buy/package-request.json``'s "Format selector contract
    (normative)" codes every lane's unsatisfied route ``UNSUPPORTED_FEATURE``. The creative
    assignment sites answer it through :func:`creative_refusal` when it carries shortfalls
    (a size or density the product does not take) and ``VALIDATION_ERROR`` themselves when
    it does not (:attr:`FormatSatisfaction.creative_coded`), because the selector contract
    governs a package's selector and not a creative's identity.

    ``entity`` names which thing the refusal is about (``creative_id``, ``product_id``,
    ``package_id``), passed through to :class:`CapabilityRefusalDetails`, which inherits
    those fields from ``EntityRefDetails``.
    """
    assert verdict.refusal is not None, "canonical_refusal called on a satisfied verdict"
    return AdCPCapabilityNotSupportedError(
        details=CapabilityRefusalDetails(
            capability=str(verdict.refusal),
            rejected_value=verdict.rejected_value,
            accepted_values=list(verdict.accepted_values),
            **entity,
        ),
        field=field,
    )


#: The refusals that are about the CREATIVES rather than the product's closed set:
#: :func:`creative_refusal` answers them with a creative code, every other one with
#: :func:`canonical_refusal`. The two manifest refusals are one creative failing the
#: declaration it selects; :attr:`FormatRefusal.PLACEMENT_UNCOVERED` reaches here only from
#: :func:`creatives_uncovered`, a package's creative set leaving an included placement
#: unfilled. All three are corrected by changing the creatives, not the buy.
_CREATIVE_REFUSALS: frozenset[FormatRefusal] = frozenset(
    {FormatRefusal.MANIFEST_NOT_SATISFIED, FormatRefusal.MANIFEST_AMBIGUOUS, FormatRefusal.PLACEMENT_UNCOVERED}
)


def creative_refusal(
    verdict: FormatSatisfaction,
    *,
    field: str | None = None,
    creative_id: str | None = None,
    **entity: Any,
) -> (
    AdCPCapabilityNotSupportedError
    | AdCPCreativeMissingClickUrlError
    | AdCPCreativeSizeMismatchError
    | AdCPCreativeValidationFailedError
    | AdCPCreativeValueNotAllowedError
):
    """The error to raise for a refused CREATIVE verdict. Every creative gate raises this.

    A selection outside the product's closed set, or an option ref it does not publish, is
    :func:`canonical_refusal`'s ``UNSUPPORTED_FEATURE``: "Reject unknown or uncovered
    selections" (creative-manifests.mdx § Validation step 5). A manifest that does not
    satisfy the declaration it selected, or a deprecated reference that names a template the
    product takes at parameters it does not (:func:`_legacy_shortfalls`), takes a creative
    code from the pinned enum (:attr:`ManifestShortfall.code`):

    * ``CREATIVE_SIZE_MISMATCH`` when every shortfall is a dimension -- "The submitted
      creative dimensions do not match any size accepted by the selected packages";
    * ``CREATIVE_MISSING_CLICK_URL`` when every shortfall is a required click destination
      left out -- "A submitted creative that requires a destination URL does not provide
      one";
    * ``CREATIVE_VALUE_NOT_ALLOWED`` when every shortfall is a text value outside a closed set
      the declaration publishes -- "A submitted text-asset value is not in the format's
      declared ``allowed_values`` list". Its entry says the seller "MUST set ``error.field``
      to the offending asset's path within the manifest" and "SHOULD include the format's
      ``allowed_values`` array in ``error.details.allowed_values``", so *field* is replaced
      by the first offending path and the set travels in ``allowed_values``;
    * ``CREATIVE_VALIDATION_FAILED_GENERIC`` otherwise, including a mix of the kinds above, an
      ambiguous selection and a creative set that leaves an included placement uncovered
      (:func:`creatives_uncovered`) -- "the producer cannot classify the failure with a more
      specific standard code". No code in the enum names a missing ``format_option_ref``;
      ``CREATIVE_REPRESENTATION_UNRESOLVED`` is about a CreativeRepresentationSet and requires
      a rejection per representation id, which a plain manifest has none of.

    The generic code's own text says producers "SHOULD prefer a specific creative code
    whenever one applies", so the error takes a specific code when every shortfall shares
    it. A refusal mixing kinds takes the generic code, and each ``problems[]`` entry still
    carries the specific code of its own shortfall.

    ``problems[]`` carries each shortfall as values -- the manifest path, what the asset
    states, what the declaration accepts -- under the code it is an instance of.
    """
    if verdict.refusal not in _CREATIVE_REFUSALS and not verdict.shortfalls:
        return canonical_refusal(verdict, field=field, creative_id=creative_id, **entity)
    codes = {shortfall.code for shortfall in verdict.shortfalls}
    common = next(iter(codes)) if len(codes) == 1 else None
    values: dict[str, Any] = {
        "creative_id": creative_id,
        "rejected_value": verdict.rejected_value,
        "accepted_values": list(verdict.accepted_values),
        "problems": [
            ErrorProblem(
                code=shortfall.code,
                subject_type="creative",
                subject_id=creative_id,
                field=shortfall.field,
                rejected_value=shortfall.rejected_value,
                accepted_values=list(shortfall.accepted_values),
            )
            for shortfall in verdict.shortfalls
        ]
        or None,
        **entity,
    }
    if common is ErrorCode.CREATIVE_VALUE_NOT_ALLOWED:
        offending = verdict.shortfalls[0]
        return AdCPCreativeValueNotAllowedError(
            details=CreativeValueNotAllowedDetails(**values, allowed_values=list(offending.accepted_values)),
            field=offending.field,
        )
    details = CreativeManifestDetails(**values)
    if common is ErrorCode.CREATIVE_SIZE_MISMATCH:
        return AdCPCreativeSizeMismatchError(details=details, field=field)
    if common is ErrorCode.CREATIVE_MISSING_CLICK_URL:
        return AdCPCreativeMissingClickUrlError(details=details, field=field)
    return AdCPCreativeValidationFailedError(details=details, field=field)


def require_covered(
    creatives: Sequence[tuple[Any, Collection[str] | None]],
    product: Any,
    *,
    field: str | None = None,
    **entity: Any,
) -> None:
    """Raise :func:`creative_refusal` when a package's creative set leaves a placement uncovered.

    The one raise for :func:`creatives_uncovered`, which every gate that changes a package's
    creatives asks once that package's whole set is known: the create path's pre-adapter gate,
    ``sync_creatives`` assignments, and ``update_media_buy``'s ``creative_ids`` and
    ``creative_assignments``. ``entity`` names the package and product the refusal is about.
    """
    verdict = creatives_uncovered(creatives, product)
    if verdict is not None:
        raise creative_refusal(verdict, field=field, **entity)


def _route_selector(route: FormatRoute) -> str:
    """The value the buyer put on the arm, rendered, so a refusal can name the route.

    One arm may carry several routes (``format_option_refs`` and ``format_ids`` are both
    arrays), so the field name alone does not identify which route a summary is about.
    Rendered rather than echoed as the object: a legacy reference carries four parameters
    the pin says are no part of which format it names, and
    ``format_resolver.format_display`` is the one spelling of that identity in this
    codebase.
    """
    from src.core.format_resolver import format_display

    claim = route.claim
    if claim.option is not None:
        return claim.option.format_option_id
    if claim.legacy is not None:
        return format_display(claim.legacy)
    return claim.kind or ""


def conflicting_selectors(
    resolution: SelectorResolution,
    *,
    field: str,
    **entity: Any,
) -> AdCPConflictingSelectorsError:
    """The ``CONFLICTING_SELECTORS`` to raise when the resolved routes disagree.

    Beside :func:`canonical_refusal` and for the same reason: the details shape is the
    pin's, so it is written once. ``enums/error-code.json``'s entry for the code states
    both halves -- "``error.field`` SHOULD point at the conflicting package, and
    ``error.details`` SHOULD identify the supplied selector routes and their normalized
    canonical summaries" -- so *field* is the PACKAGE pointer rather than an arm, which is
    the one way this refusal's pointer differs from every other refusal on this path:
    no single arm is at fault, the set of them is.

    Every route is summarized, not the disagreeing pair; see
    :class:`SelectorConflictDetails`. A route is an ARM (:attr:`SelectorResolution._arms`),
    so each summary carries the arm's whole selected option SET -- the thing step (2)
    compares -- and an arm whose entries selected nothing carries an empty one.
    """
    assert resolution.conflicting, "conflicting_selectors called on a resolution that does not conflict"
    return AdCPConflictingSelectorsError(
        details=SelectorConflictDetails(
            selector_routes=[_arm_summary(pairs) for pairs in resolution._arms],
            **entity,
        ),
        field=field,
    )


def _arm_summary(pairs: Sequence[tuple[FormatRoute, FormatSatisfaction]]) -> SelectorRouteSummary:
    """One arm as the buyer sent it and as it normalized: its values, shapes and selected set.

    ``format_kind`` is the canonical shape each entry resolved to: the selected declarations'
    kinds, or, for an entry that selected nothing, the kind the entry itself named -- which
    for a legacy reference is what ``canonical_kind_for`` projected. Each list keeps first-seen
    order and names a value once.
    """
    kinds: list[str] = []
    for route, verdict in pairs:
        if verdict.selection:
            kinds.extend(str(d.format_kind) for d in verdict.selection)
        elif route.claim.kind is not None:
            kinds.append(route.claim.kind)
    return SelectorRouteSummary(
        field=pairs[0][0].field or "",
        selector=_distinct(_route_selector(route) for route, _ in pairs),
        format_kind=_distinct(kinds),
        selected_option=_distinct(_declaration_label(d) for _, verdict in pairs for d in verdict.selection),
    )


def _distinct(values: Iterable[str]) -> list[str]:
    """*values* in first-seen order, each once."""
    return list(dict.fromkeys(values))
