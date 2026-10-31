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
from typing import Any, get_args

import factory
from adcp.types import FormatAssetUnion, RepeatableAssetGroup, Responsive
from adcp.types.domains.core.format import (  # TODO: no stable alias in adcp.types
    Dimensions,
    DisclosureCapability,
    Renders,
)
from pydantic import BaseModel

from src.core.creative_agent_registry import PUBLIC_DEFAULT_AGENT_URL
from src.core.schemas import Format, FormatId

#: Derived, not re-typed -- see ``tests.factories.creative.DEFAULT_CREATIVE_AGENT_URL``, which
#: is the same value under the name the step modules import. Both are the federated identity
#: the catalog's own entries declare; neither is a destination.
AGENT_URL = PUBLIC_DEFAULT_AGENT_URL

# ── Asset class mapping ──────────────────────────────────────────────


def _asset_classes_by_type(model: type[BaseModel], field: str = "assets") -> dict[str, type[BaseModel]]:
    """``asset_type`` -> concrete asset class, read off a pinned discriminated union.

    One derivation serves both asset unions — the top-level ``Format.assets`` one and
    the narrower one inside a ``repeatable_group``. Reading the model rather than
    restating it is not a stylistic choice here: the generated classes are NUMBERED
    (``Assets``, ``Assets9``, ``Assets31``, ...), the numbers shift on SDK
    regeneration, and the group's inner classes carry no ``*FormatAsset`` alias at
    all, so there is nothing stable to hand-write them against.

    The walk is shape-insensitive on purpose: it recurses the whole annotation
    instead of assuming a nesting depth. The registry's deleted ``_known_asset_types``
    was the cautionary case — it hand-unwrapped ``list[Union[...]] | None``, the SDK
    added a ``Discriminator`` layer around the union, and it returned an EMPTY
    frozenset from then on, silently. Hence the emptiness check below: a derivation
    that matches nothing is green.
    """
    found: dict[str, type[BaseModel]] = {}

    def walk(tp: object) -> None:
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            default = getattr(tp.model_fields.get("asset_type"), "default", None)
            if isinstance(default, str):
                found.setdefault(default, tp)
        for arg in get_args(tp):
            walk(arg)

    walk(model.model_fields[field].annotation)
    if not found:
        raise RuntimeError(
            f"{model.__name__}.{field} yielded no asset classes. The pinned union's shape "
            "changed and this walk no longer finds it — fix the walk rather than letting "
            "the factory build assets out of an empty vocabulary."
        )
    return found


#: Every asset type the generated ``Format.assets`` union models — 14 at adcp
#: 9.0.0b2. This was a hand-written 6 of 14, with ``make_asset`` falling back to the
#: image class for the other 8 (salesagent-xwhsk).
#:
#: The SDK union is NARROWER than the schema it ships beside:
#: ``_schemas/3.2/core/format.json`` admits 21 individual asset types (the 14 plus
#: zip, display_tag, published_post, card, pixel_tracker, vast_tracker,
#: daast_tracker), and ``enums/asset-content-type.json`` lists 16. The factory
#: builds SDK objects that have to validate, so the SDK union is its ceiling —
#: a scenario needing one of the other 7 is blocked on SDK generation, not on this
#: map. Do not widen this by hand to close that gap; a hand-written entry has no
#: class to point at.
_ASSET_CLASS_MAP = _asset_classes_by_type(Format)

#: Every asset type the generated in-group union models. Derived, so it tracks
#: whatever the pinned SDK generates.
#:
#: This comment used to read "12, omitting brief and catalog ... that omission is
#: the SDK's, not the schema's". At the 3.2 pin there is NO omission: the
#: repeatable_group arm of core/format.json declares all 21 and the generated
#: group union models all 21, which
#: ``test_format_factory_refuses_unknown_asset`` now derives and compares on both
#: sides rather than restating (salesagent-8qela.168).
_GROUP_ASSET_CLASS_MAP = _asset_classes_by_type(RepeatableAssetGroup)

# The asset-type vocabulary this factory can build, for steps that need to check
# a claimed asset type is real rather than build one. Derived, not restated.
ASSET_TYPES = frozenset(_ASSET_CLASS_MAP)

#: The same for the in-group union. Public so a guard can compare it against the
#: pinned schema's own repeatable_group arm without reaching into a private name.
GROUP_ASSET_TYPES = frozenset(_GROUP_ASSET_CLASS_MAP)


def make_asset(asset_type: str, asset_id: str | None = None, **fields: Any) -> FormatAssetUnion:
    """Create a typed asset object from an asset type string.

    *fields* are further members of the asset slot the pinned class declares -- e.g.
    ``requirements=ImageAssetRequirements(max_file_size_kb=2048)`` -- validated by that
    class like the two this function always sets.

    Refuses an asset type it cannot build. This used to resolve the class with
    ``.get(asset_type, ImageFormatAsset)``, so a Given asking for a video, audio
    or html asset was handed an IMAGE asset and every assertion downstream graded
    the image path — a wrong-object pass, not a visible gap. A factory that
    substitutes an object for the one asked for cannot fail.

    >>> a = make_asset("video")
    >>> a.asset_type
    'video'
    """
    try:
        cls = _ASSET_CLASS_MAP[asset_type]
    except KeyError:
        raise ValueError(
            f"make_asset cannot build asset type {asset_type!r}. The generated "
            f"Format.assets union models: {sorted(_ASSET_CLASS_MAP)}. A type outside "
            "that list has no SDK class to instantiate, so there is nothing to seed — "
            "check core/format.json: if the schema declares it, this is an SDK "
            "generation gap; if not, the scenario is asking for an asset that does not "
            "exist at this spec version."
        ) from None
    return cls(asset_id=asset_id or f"{asset_type}_asset", required=True, **fields)


def make_disclosure_capabilities(*entries: tuple[str, list[str]]) -> list[DisclosureCapability]:
    """``disclosure_capabilities`` entries, one per ``(position, persistence modes)`` pair.

    ``core/format.json`` requires both members of an entry ("required": ["position",
    "persistence"], persistence ``minItems: 1``), which the pinned class enforces, and says
    "Each position MUST appear at most once", which this enforces.
    """
    positions = [position for position, _ in entries]
    assert len(positions) == len(set(positions)), f"a disclosure position may appear once, got {positions}"
    return [DisclosureCapability(position=position, persistence=modes) for position, modes in entries]


def _find_inner_asset_class(asset_type: str) -> type[BaseModel]:
    """The inner asset class a repeatable group holds for *asset_type*, or raise.

    The generated in-group union is narrower than the top-level one (no brief, no
    catalog), so a type can be buildable at the top level and not inside a group.
    This used to fall back to the union's FIRST member (an image asset), so a group
    asked for two types could come back holding two of the wrong kind.
    """
    try:
        return _GROUP_ASSET_CLASS_MAP[asset_type]
    except KeyError:
        raise ValueError(
            f"a repeatable asset group cannot hold asset type {asset_type!r}. The "
            f"generated in-group union models: {sorted(_GROUP_ASSET_CLASS_MAP)}."
        ) from None


def make_asset_group(
    *asset_types: str,
    group_id: str = "asset_group",
    min_count: int = 1,
    max_count: int = 10,
):
    """Create a repeatable asset group containing typed inner assets.

    The inner classes are looked up by asset_type in a map derived from the pinned
    group union, which is what survives SDK regeneration renumbering the generated
    class names (Assets18→Assets94, etc.).
    """
    # No `if inner_cls` guard: _find_inner_asset_class raises rather than returning
    # None, so a group never comes back holding fewer assets than it was asked for.
    inner_assets = [_find_inner_asset_class(at)(asset_id=f"{at}_asset", required=True) for at in asset_types]
    return RepeatableAssetGroup(
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


def resolvable_format_ref(format_id: str, **parameters: int) -> dict[str, Any]:
    """A creative's claimed legacy reference, CHECKED against the catalog that answers it.

    ``parameters`` carries the reference's own parameterizing fields, which is what turns a
    TEMPLATE reference into a reference to one VARIANT -- ``core/format-id.json``: an id "used
    alone, references a template format. When combined with dimension/duration fields, creates
    a parameterized format ID for a specific variant." They are spread onto the reference
    rather than nested beside it, because the pin declares them on the reference object.

    The accepted names come from production's own ``_FORMAT_ID_PARAMETERS``, which derives them
    off the pinned model. Two hand-written lists of these fields have each already dropped
    ``pixel_ratio`` -- that constant exists because of the first, and an earlier SDK's
    ``format_is_supported`` compared only three of the four. Importing it rather than
    re-deriving is what keeps this from being the third chance to omit one.

    Catalog membership is a property of the ID ALONE, so parameters do not widen or narrow the
    check below: a variant of a served template is served. What they DO change is acceptance,
    which this door still does not promise -- see the next paragraph but one.

    THE door for authoring a ``format_id`` a creative claims. A claimed reference is the one
    format literal in a fixture that production RESOLVES over the wire -- ``_validation.py``
    hands it to ``fetch_format_spec`` and refuses the creative REFERENCE_NOT_FOUND when the
    agent does not serve it. A product's ``format_ids`` arm is a different thing: it is
    compared as an identity and never dialled, so it is legitimately free of the catalog and
    does not come through here.

    Refusing here is what makes an unserved id visible IN PROCESS. The in-process harness
    normally mocks the registry to answer every resolution with one stub, so a claim naming a
    format nobody serves reads green on three transports and fails only on e2e_rest, where
    the resolution is real -- which is how ``video_640x480`` survived in
    BR-UC-FORMAT-SATISFACTION until a box run graded it (#1418, the capture's own issue).

    The refusal is shaped after ``tests/integration/test_creative_agent_live.py``'s: say what
    is wrong, say where the authority is, print the remedy.

    CLEARING THIS DOOR DOES NOT MEAN THE CREATIVE WILL BE ACCEPTED. Resolvability and
    satisfaction are two different gates, and ``satisfies()`` never consults the capture at all
    (measured by rfc-s1-admin on the create path, salesagent-7byez): an id can be
    SATISFYING BUT UNSERVED -- ``display_300x250`` passes the satisfaction gate and this door
    refuses it -- or SERVED BUT UNSATISFYING -- ``display_728x90_image`` clears this door and is
    refused against a 300x250 declaration.

    They cannot be collapsed because acceptance is a DIFFERENT COMPARISON, and which one runs
    depends on the product's arms: ``_canonical_lane`` PROJECTS a legacy claim through
    ``upgrade_legacy_format_id`` and grades the upgraded dimensions, so the id need not equal
    anything, while ``_legacy_lane`` tests whole-identity membership (``claimed in legacy_keys``)
    with no projection at all. So when a product declares ONLY ``format_ids`` and that arm names
    a non-catalog id, the two gates are MUTUALLY UNSATISFIABLE: satisfaction demands exactly the
    id this door forbids. That is not a fixture quirk and must not be relaxed to unblock a row --
    ``_legacy_lane``'s own docstring records why it is deliberately not widened.

    Consequence for picking an id: read what the scenario ASSERTS first. A scenario asserting a
    REFUSAL needs only resolvability, so any captured id will do -- which is why salesagent-3oo5h
    was a one-literal fix. A scenario asserting an ACCEPT needs both gates, so the product's arm
    must be a catalog id too (salesagent-rkuzz is nine rows where it is not).

    WHAT THIS DOES NOT PROMISE EITHER: that the format is USABLE, only that it RESOLVES. Membership
    and derivability are different sets, and they barely overlap -- rfc-gamph measured that 49
    of the 71 captured ids derive dimensions from no available primitive (salesagent-lyh1i).
    The relation is close to INVERSE, measured on the four ids this feature touches:

        video_1280x720        IN the capture  | canonical_kind_for RAISES, no dimensions
        display_300x250_image IN the capture  | kind RAISES, upgrades to display_image 300x250
        video_640x480         NOT in capture  | kind video_hosted, no dimensions
        display_300x250       NOT in capture  | kind image, upgrades to display_image 300x250

    "RAISES" above means from the id ALONE, which is the only question this door asks. A
    captured id is still PROJECTABLE through the format's own ``canonical`` declaration --
    measured, 69 of the 76 declare one, ``video_1280x720`` among them -- so a caller who has
    the catalog resolves it at step 1. What the caller needs is to actually HAVE the catalog:
    the sync path does (``find_format(all_formats, ...)``), ``CreativeFactory`` does through
    ``served_format_spec``, and a bare ``canonical_kind_for(id)`` does not.

    So the ids the primitives know are largely the hand-invented legacy ones the agent does
    NOT serve, and vice versa. A caller needing DIMENSIONS wants the ``display_NNNxNNN_image``
    family, the only one ``upgrade_legacy_format_id`` sizes; a caller needing only a resolvable
    identity -- which is what a legacy-lane claim needs, since ``_legacy_lane`` compares whole
    identities and reads no dimensions -- can take any captured id. Do not add a third
    dimension-from-id derivation to narrow this: salesagent-lyh1i owns collapsing the two that
    already exist.
    """
    from src.core.format_cache import load_reference_formats

    catalog = {fmt.format_id.id for fmt in load_reference_formats()}
    if format_id not in catalog:
        raise ValueError(
            f"{format_id!r} is not served by the creative agent this run reaches, so a creative "
            "claiming it is refused REFERENCE_NOT_FOUND. The authority is the pinned capture "
            "tests/fixtures/creative_formats/reference_formats.json "
            f"({len(catalog)} formats). Name one of those, or select by property with "
            "pick_reference_format()/pick_reference_formats(); if the format SHOULD exist, "
            "register it in the creative agent's own registry and recapture with "
            "scripts/refresh-reference-formats.py. Do not reach for a literal the catalog "
            "does not carry -- the in-process registry mock resolves anything, so it would "
            "read green here and fail only on e2e_rest."
        )
    from src.core.schemas._base import _FORMAT_ID_PARAMETERS

    if unknown := set(parameters) - _FORMAT_ID_PARAMETERS:
        raise ValueError(
            f"{sorted(unknown)} are not parameterizing fields of a format reference. "
            f"core/format-id.json declares {sorted(_FORMAT_ID_PARAMETERS)} beside agent_url and id, "
            "read off the pinned model rather than listed here. A reference carries its parameters "
            "on itself, so a field the pin does not declare would be silently dropped by the "
            "accepted-shape strip instead of refused."
        )
    return {"agent_url": AGENT_URL, "id": format_id, **parameters}


def _canonical_kind_of(fmt: Format) -> str | None:
    """The canonical format kind a reference format declares, as a plain string.

    THIS USED TO READ ``fmt.type``, AND ``type`` IS NOT A FIELD. It was a key the v3.1.25
    capture carried on every one of its 71 entries, and the pinned ``LibraryFormat``'s
    ``extra="allow"`` STORED it, so a plain attribute read found it. The local subclass now
    sets ``extra="ignore"`` -- correctly, because deleting a declared field from an
    ``extra="allow"`` parent promotes it to an extra that reaches the wire -- and the key is
    dropped on validation. Measured at that point: ``_category_of`` answered ``None`` for
    all 71 formats, so ``pick_reference_format`` matched nothing and the UC-005
    multi-category Given raised (salesagent-6018h).

    ``canonical.kind`` is the pinned replacement and not merely an available one:
    ``core/format-id.json`` makes it required when a format declares a projection, the
    capture declares one on 69 of the 76, and it is the same vocabulary everything inside
    this seller keys on. Reading a field the pin declares is what stops the next ``extra``
    tightening from silently emptying this selector again.
    """
    value = getattr(getattr(fmt, "canonical", None), "kind", None)
    return None if value is None else str(getattr(value, "value", value))


def _uses_asset_type(fmt: Format, asset_type: str) -> bool:
    return any(
        str(getattr(getattr(a, "asset_type", None), "value", getattr(a, "asset_type", None))) == asset_type
        for a in fmt.assets or []
    )


def asset_types_of(fmt: Any) -> set[str]:
    """Every asset type a format declares, individual slots and repeatable-group members alike.

    The asset_types filter matches both (``list-creative-formats-request.json``: "Filter
    to formats that include these asset types"), so a format carrying a type only inside a
    group still includes it.

    *fmt* is a ``Format`` or the JSON object a buyer received for one: a Then grading the
    wire reads the same members off the serialized body with the same walk.
    """

    def member(obj: Any, name: str) -> Any:
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    types: set[str] = set()
    for slot in member(fmt, "assets") or []:
        for asset in (slot, *(member(slot, "assets") or [])):
            asset_type = member(asset, "asset_type")
            if asset_type is not None:
                types.add(str(getattr(asset_type, "value", asset_type)))
    return types


def pick_reference_format(canonical_kind: str, *, without_asset_type: str | None = None) -> Format:
    """The first reference format declaring *canonical_kind*, in catalog order.

    A scenario that says "an image format" wants a REAL one -- served by the in-process
    fixture and the live e2e stack alike -- not a minted ``fmt_N`` that the live stack can
    never realize and that carries no assets to grade. Catalog order makes the choice
    deterministic across runs and transports.

    THE SELECTOR IS A CANONICAL KIND and not the capture's ``type`` string, for the reason
    :func:`_canonical_kind_of` records. Measured on the pinned capture, excluding the
    ``pixel_tracker`` carriers: ``image`` 14, ``video_hosted`` 3, ``video_vast`` 2, and
    ``html5`` / ``audio_hosted`` 0 -- every html5 and audio format the agent serves carries
    a pixel_tracker asset the pin's assets union does not admit, so those two kinds are
    unreachable through this helper today.

    ``without_asset_type`` skips formats carrying that asset type; it exists for
    ``pixel_tracker``, which the pinned 3.1.1 ``core/format.json`` assets union does not
    admit (adcp#7338) although the reference agent serves it, so a scenario whose Then
    validates the response against the pin can still pick a format the pin accepts.
    """
    return pick_reference_formats(
        lambda f: (
            _canonical_kind_of(f) == canonical_kind
            and (without_asset_type is None or not _uses_asset_type(f, without_asset_type))
        )
    )[0]


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
